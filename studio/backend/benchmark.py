"""Benchmark QC rules against ground truth (Target: Precision >= 0.85, Recall >= 0.80).

Supports:
- Team's injection format (<video>.truth.json): [{type, a, b, frame}]
- Native idcheck GT format: [{type, frame, track_id}]
- Baseline subtraction: issues already present in the baseline/clean data are ignored as false alarms.
- 1-to-1 greedy matching with tolerance window.
- Overall and per-error-type breakdown with Missed and False Alarm frame links.
"""
import copy
import json
import logging
import random
import time
from pathlib import Path

from studio_core.models import normalize_boxes

logger = logging.getLogger(__name__)

KINDS = {
    'id_switch': 'ID Switch Candidate',
    'gap': 'Gap Frame',
    'jump': 'Box Jump',
}
MAX_PER_KIND = 20

# Team's injection types mapping: [{type, a, b, frame}]
TEAM_TYPES = {
    'fragment': ('ID Switch Candidate', 6),   # a ends at frame, b starts a few frames later
    'switch': ('ID Switch Candidate', 0),
    'gap': ('Gap Frame', 2),                  # frame = last frame before the gap
    'jump': ('Box Jump', 0),
    'class_flip': ('Class Change', 0),
    'duplicate': ('Duplicate Track/Frame', 0),
}


def normalize_gt(events):
    """Chuẩn hóa dữ liệu GT: chấp nhận cả dạng {type, a, b, frame} và {type, frame, track_id}."""
    out = []
    for e in events:
        if not isinstance(e, dict):
            continue
        if 'a' in e and 'track_id' not in e:
            kind = str(e.get('type', '')).lower()
            if kind not in TEAM_TYPES:
                logger.warning("Loại lỗi GT không xác định: %s", e.get("type"))
                continue
            t, after = TEAM_TYPES[kind]
            tracks = [int(e['a'])] + ([int(e['b'])] if e.get('b') is not None else [])
            ab = f"{e['a']}→{e['b']}" if e.get('b') is not None else f"{e['a']}"
            out.append({
                'type': t,
                'frame': int(e['frame']),
                'track_id': tracks[0],
                'tracks': tracks,
                'frame_to': int(e['frame']) + after,
                'planted': f'{kind} {ab}'
            })
        else:
            try:
                tracks = [int(e['track_id'])]
                if 'pair' in e and isinstance(e['pair'], list):
                    tracks.extend([int(x) for x in e['pair']])
                out.append({
                    **e,
                    'frame': int(e['frame']),
                    'track_id': int(e['track_id']),
                    'tracks': tracks
                })
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Bỏ qua dòng GT lỗi định dạng: %s (%s)", e, exc)
    return out


def _hits(p, g, tol):
    if p['type'] != g['type']:
        return False
    tracks = set(g.get('tracks') or [g['track_id']])
    pred_tracks = {int(p['track_id'])}
    if p.get('pair') and isinstance(p['pair'], list):
        pred_tracks.update(int(x) for x in p['pair'])
    if not (tracks & pred_tracks):
        return False
    return int(g['frame']) - tol <= int(p['frame']) <= int(g.get('frame_to', g['frame'])) + tol


def _match(pred, gt, tol):
    """So khớp 1-1 tham lam (greedy) giữa predicted issues và GT."""
    used, pairs = set(), []
    for pi, p in enumerate(pred):
        for gi, g in enumerate(gt):
            if gi not in used and _hits(p, g, tol):
                used.add(gi)
                pairs.append((pi, gi))
                break
    return pairs


def _score(pred, gt, tol):
    tp = len(_match(pred, gt, tol))
    fp = len(pred) - tp
    fn = len(gt) - tp
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
    return {
        'tp': tp,
        'fp': fp,
        'fn': fn,
        'precision': round(prec, 4),
        'recall': round(rec, 4),
        'f1': round(f1, 4),
        'target_met': (prec >= 0.85 and rec >= 0.80)
    }


def evaluate(req, _actor=None):
    """Quét dữ liệu hiện tại và đối soát kết quả với Ground Truth (có trừ baseline)."""
    from studio.backend.orchestrator import scan
    
    gt_raw = req.get('ground_truth')
    if not isinstance(gt_raw, list) or not gt_raw:
        raise ValueError('Dữ liệu GT cần danh sách các sự kiện lỗi: [{type, frame, track_id}, …]')
    
    gt = normalize_gt(gt_raw)
    if not gt:
        raise ValueError('Không có mục GT hợp lệ nào sau khi chuẩn hóa.')
        
    tol = max(0, min(10, int(req.get('tolerance', 2))))
    
    # Lấy danh sách boxes hiện tại (từ body hoặc từ project_id)
    boxes = req.get('boxes')
    if boxes is None and req.get('project_id'):
        from backend.projects import get
        project = get(req['project_id'])
        state = project.get('state') or {}
        boxes = state.get('boxes') or []
        meta = state.get('meta') or {}
    else:
        meta = req.get('meta') or {}
        
    if not boxes:
        raise ValueError('Không có annotation nào để quét và đánh giá.')
        
    # Quét bằng rule engine của idcheck
    scan_res = scan({
        'boxes': boxes,
        'width': meta.get('width'),
        'height': meta.get('height'),
        'mode': meta.get('mode', 'video'),
        'total_frames': meta.get('total_frames'),
        'frame_base': meta.get('frame_base'),
        'fps': float(meta.get('fps') or 5.0),
        'config': req.get('config')
    })
    
    issues = scan_res['issues']
    pred = []
    for i in issues:
        pair = (i.get('fix_data') or {}).get('pair') or []
        pred.append({
            'id': i.get('id'),
            'type': i.get('type'),
            'frame': i.get('frame'),
            'track_id': i.get('track_id'),
            'message': i.get('message'),
            'severity': i.get('severity'),
            'pair': pair
        })
        
    # Xử lý Baseline subtraction (những lỗi vốn đã có từ dữ liệu gốc không tính là FP)
    baseline_raw = req.get('baseline') or []
    baseline = normalize_gt(baseline_raw)
    if req.get('baseline_boxes'):
        base_scan = scan({
            'boxes': req['baseline_boxes'],
            'width': meta.get('width'),
            'height': meta.get('height'),
            'mode': meta.get('mode', 'video'),
            'total_frames': meta.get('total_frames'),
            'frame_base': meta.get('frame_base'),
            'fps': float(meta.get('fps') or 5.0)
        })
        baseline.extend(normalize_gt([
            {'type': i['type'], 'frame': i['frame'], 'track_id': i['track_id']}
            for i in base_scan['issues']
        ]))
        
    if baseline:
        base_pairs = _match(pred, baseline, tol)
        matched_preds = {pi for pi, _ in base_pairs}
        pred = [p for i, p in enumerate(pred) if i not in matched_preds]
        
    if req.get('only_gt_types', True):
        gt_types = {g['type'] for g in gt}
        pred = [p for p in pred if p['type'] in gt_types]
        
    total = _score(pred, gt, tol)
    pairs = _match(pred, gt, tol)
    hit_p = {p for p, _ in pairs}
    hit_g = {g for _, g in pairs}
    
    by_type = {}
    all_types = sorted({g['type'] for g in gt} | {p['type'] for p in pred})
    for t in all_types:
        sub_p = [p for p in pred if p['type'] == t]
        sub_g = [g for g in gt if g['type'] == t]
        by_type[t] = _score(sub_p, sub_g, tol)
        
    return {
        **total,
        'tolerance': tol,
        'by_type': by_type,
        'predicted_count': len(pred),
        'ground_truth_count': len(gt),
        'baseline_ignored': len(baseline),
        'target_requirement': 'Precision >= 0.85 & Recall >= 0.80',
        'missed': [g for i, g in enumerate(gt) if i not in hit_g],
        'false_alarms': [p for i, p in enumerate(pred) if i not in hit_p]
    }
