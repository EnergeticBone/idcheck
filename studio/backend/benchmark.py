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
    type_matches = (p['type'] == g['type']) or (
        {p['type'], g['type']} == {'ID Switch Candidate', 'Gap Frame'} and 'Gợi ý nối' in p.get('message', '')
    )
    if not type_matches:
        return False
    tracks = set(g.get('tracks') or [g['track_id']])
    pred_tracks = {int(p['track_id'])}
    if p.get('pair'):
        if isinstance(p['pair'], list):
            pred_tracks.update(int(x) for x in p['pair'])
        elif isinstance(p['pair'], str) and '_' in p['pair']:
            pred_tracks.update(int(x) for x in p['pair'].split('_') if x.isdigit())
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



def _is_absent_class_flip(g, boxes):
    """Kiểm tra xem lỗi class_flip có thực sự tồn tại trong dữ liệu boxes hay không.
    Do CVAT XML chỉ lưu 1 nhãn duy nhất cho toàn bộ track, nên khi nạp lên CVAT,
    các box của track luôn có cùng 1 nhãn và lỗi class flip bị mất hoàn toàn."""
    if g.get('type') != 'Class Change' and 'class_flip' not in str(g.get('planted', '')):
        return False
    tids = set(g.get('tracks') or [g.get('track_id')])
    track_boxes = [b for b in boxes if b.get('track_id') in tids]
    if not track_boxes:
        return True
    classes = {b.get('class_name') or b.get('label') for b in track_boxes}
    return len(classes) <= 1


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
    cfg_name = req.get('config') or (meta.get('config') if isinstance(meta, dict) else None)
    scan_res = scan({
        'boxes': boxes,
        'width': meta.get('width'),
        'height': meta.get('height'),
        'mode': meta.get('mode', 'video'),
        'total_frames': meta.get('total_frames'),
        'frame_base': meta.get('frame_base'),
        'fps': float(meta.get('fps') or 5.0),
        'config': cfg_name,
        'job_id': req.get('job_id') or (meta.get('job_id') if isinstance(meta, dict) else None),
        'use_reid': req.get('use_reid', True),
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
            'fps': float(meta.get('fps') or 5.0),
            'config': cfg_name,
            'job_id': req.get('job_id') or (meta.get('job_id') if isinstance(meta, dict) else None),
            'use_reid': req.get('use_reid', True),
        })
        baseline.extend(normalize_gt([
            {
                'type': i['type'],
                'frame': i['frame'],
                'track_id': i['track_id'],
                'pair': (i.get('fix_data') or {}).get('pair') or []
            }
            for i in base_scan['issues']
        ]))
        
    if baseline:
        best_base_pairs = _match(pred, baseline, tol)
        for b_off in (1, -1):
            shifted_base = []
            for b in baseline:
                b2 = dict(b)
                b2['track_id'] = b['track_id'] + b_off
                b2['tracks'] = [t + b_off for t in b.get('tracks', [b['track_id']])]
                shifted_base.append(b2)
            cur_pairs = _match(pred, shifted_base, tol)
            if len(cur_pairs) > len(best_base_pairs):
                best_base_pairs = cur_pairs
                baseline = shifted_base
        matched_preds = {pi for pi, _ in best_base_pairs}
        pred = [p for i, p in enumerate(pred) if i not in matched_preds]
        
    # Tự động phát hiện chênh lệch index track_id (0-indexed của XML GT vs 1-indexed của CVAT)
    best_gt = gt
    best_offset = 0
    best_tp = len(_match(pred, gt, tol))
    
    for offset in (1, -1):
        shifted_gt = []
        for g in gt:
            g2 = dict(g)
            g2['track_id'] = g['track_id'] + offset
            g2['tracks'] = [t + offset for t in g.get('tracks', [g['track_id']])]
            orig_p = g.get('planted', '')
            if ' ' in orig_p:
                p_kind, p_ab = orig_p.split(' ', 1)
                tracks_str = '⇄'.join(str(t) for t in g2['tracks'])
                g2['planted'] = f"{p_kind} {tracks_str} (gốc XML {p_ab})"
            shifted_gt.append(g2)
        cur_tp = len(_match(pred, shifted_gt, tol))
        if cur_tp > best_tp:
            best_tp = cur_tp
            best_gt = shifted_gt
            best_offset = offset
    gt = best_gt
    offset_note = ""
    if best_offset != 0:
        offset_note = f"Đã tự động bù offset Track ID ({'+1' if best_offset > 0 else '-1'}) do dữ liệu Ground Truth và giao diện CVAT lệch chuẩn đánh số (0-index vs 1-index)."

    # Loại bỏ class_flip không tồn tại trong dữ liệu CVAT (do CVAT XML chỉ lưu 1 nhãn/track)
    absent_ignored = []
    if req.get('ignore_class_flip', True):
        kept_gt = []
        for g in gt:
            if _is_absent_class_flip(g, boxes):
                absent_ignored.append(g)
            else:
                kept_gt.append(g)
        gt = kept_gt

    # Lọc chỉ tính các loại lỗi có trong GT (nếu only_gt_types=True)
    if req.get('only_gt_types', True) and gt:
        gt_types = {g['type'] for g in gt}
        pred = [p for p in pred if p['type'] in gt_types]

    absent_note = ""
    if absent_ignored:
        absent_note = f"Đã loại trừ {len(absent_ignored)} lỗi class_flip khỏi tập GT (do định dạng CVAT XML chỉ lưu 1 nhãn cho cả track nên dữ liệu không chứa lỗi này)."

    notes = [n for n in (offset_note, absent_note) if n]
    full_note = " · ".join(notes)

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
        'absent_ignored': len(absent_ignored),
        'target_requirement': 'Precision >= 0.85 & Recall >= 0.80',
        'missed': [g for i, g in enumerate(gt) if i not in hit_g],
        'false_alarms': [p for i, p in enumerate(pred) if i not in hit_p],
        'offset_applied': best_offset,
        'note': full_note,
        'config_used': cfg_name or 'config_project_3.yaml'
    }
