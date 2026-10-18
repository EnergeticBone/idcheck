"""Application services. Powered by idcheck core rule engine."""
from collections import Counter
from pathlib import Path
import yaml

from studio_core.parsers import parse_file
from studio_core.exporters import export_mot, export_cvat
from studio_core.models import normalize_boxes, issue
from studio_core.fixing.operations import apply_fix
from backend import audit

from core.schema import Track, Box
from core.rules import run_rules
from core.merge import suggest_merges
from core.config import resolve_cfg

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECT_CONFIG_PATH = REPO_ROOT / "config_project_3.yaml"
BASE_CONFIG_PATH = REPO_ROOT / "config_base.yaml"


def _load_idcheck_cfg(fps=5.0, config_name=None):
    cfg_file = None
    if config_name:
        cand = REPO_ROOT / config_name
        if cand.is_file():
            cfg_file = cand
    if not cfg_file:
        cfg_file = PROJECT_CONFIG_PATH if PROJECT_CONFIG_PATH.is_file() else BASE_CONFIG_PATH

    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            raw_cfg = yaml.safe_load(f) or {}
    except Exception:
        raw_cfg = {}
    return resolve_cfg(raw_cfg, fps)


def parse(body):
    content = body.get('content', '')
    if not isinstance(content, str) or len(content) > 25_000_000:
        raise ValueError('File nhãn quá lớn (tối đa 25 MB).')
    data = parse_file(str(body.get('filename', 'annotations.txt')), content, body.get('labels_text', ''))
    data['session'] = audit.new_session()
    return data


def scan(body):
    rows = normalize_boxes(body.get('boxes', []))
    width = body.get('width')
    height = body.get('height')
    mode = body.get('mode', 'video')
    total_frames = body.get('total_frames')
    frame_base = body.get('frame_base')
    fps = float(body.get('fps') or 5.0)
    config_name = body.get('config')

    frame_size = (float(width), float(height)) if width and height and float(width) > 0 and float(height) > 0 else None
    cfg = _load_idcheck_cfg(fps, config_name)

    # Chuyển đổi boxes của Web sang đối tượng Track của idcheck
    tracks = {}
    for b in rows:
        tid = int(b['track_id'])
        fid = int(b['frame_id'])
        lbl = str(b.get('class_name') or 'Car')
        if tid not in tracks:
            tracks[tid] = Track(id=tid, label=lbl, boxes={})
        tracks[tid].boxes[fid] = Box(
            frame=fid,
            x1=float(b['x']),
            y1=float(b['y']),
            x2=float(b['x'] + b['w']),
            y2=float(b['y'] + b['h']),
            label=lbl,
            outside=bool(b.get('outside', False)),
            occluded=bool(b.get('occluded', False)),
            keyframe=bool(b.get('keyframe', True))
        )

    # Chạy bộ rule tối ưu của idcheck
    raw_errs = run_rules(tracks, cfg, frame_size=frame_size)

    # Ánh xạ kết quả sang format Issue của giao diện Studio
    issues = []
    for err in raw_errs:
        t = err.get('type')
        f = int(err.get('frame', 0))
        tid = int(err.get('track_id', 0))
        detail = str(err.get('detail', ''))

        if t == 'invalid_box':
            issues.append(issue('Invalid Box', f, tid, detail, severity='high'))
        elif t == 'gap':
            gap_len = int(err.get('gap', 0))
            sev = 'high' if gap_len > 10 else 'medium'
            sugg = 'interpolate_gap' if 2 <= gap_len <= 4 else 'review'
            data = {'track_id': tid, 'from_frame': f, 'to_frame': f + gap_len}
            issues.append(issue('Gap Frame', f, tid, detail, severity=sev, suggestion=sugg, data=data))
        elif t == 'jump':
            issues.append(issue('Box Jump', f, tid, detail, severity='high'))
        elif t == 'class_flip':
            expected_class = err.get('expected_class') or (tracks[tid].label if tid in tracks else 'Car')
            data = {'track_id': tid, 'class_name': expected_class}
            issues.append(issue('Class Change', f, tid, detail, severity='high', suggestion='set_class', data=data))
        elif t == 'duplicate':
            other_id = err.get('other_id')
            data = {'frame_id': f, 'track_id': tid, 'pair': f"{tid}_{other_id}"}
            issues.append(issue('Duplicate Track/Frame', f, tid, detail, severity='high',
                                suggestion='remove_exact_duplicates', data=data))
        elif t == 'switch':
            other_id = err.get('other_id')
            data = {'a': tid, 'b': other_id, 'from_frame': f, 'source': other_id, 'target': tid, 'pair': f"{tid}_{other_id}"}
            issues.append(issue('ID Switch Candidate', f, tid, detail, severity='high',
                                suggestion='swap_ids', data=data))

    # Gợi ý nối track (Merge suggestions tương đương CLI watch)
    if body.get('include_merges', True):
        min_score = float(body.get('min_score', 0.5))
        raw_merges = suggest_merges(tracks, cfg, min_score=min_score, frame_size=frame_size)
        for m in raw_merges:
            ta, tb, gap, score = m['a'], m['b'], m['gap'], m['score']
            f = tracks[ta].end if ta in tracks else 0
            issues.append(issue('Gap Frame', f, ta,
                                f"Gợi ý nối track {ta} ➜ {tb} (gap {gap} frames, score {score:.2f})",
                                severity='medium', data={'pair': f"{ta}_{tb}", 'target': tb, 'gap': gap}))

    # Visual Re-ID verification & enhancement
    frame_getter = body.get('frame_getter')
    job_id = body.get('job_id')
    token = body.get('token') or body.get('video_token')

    if frame_getter is None and job_id:
        try:
            from studio.backend.cvat_workflow import get_job_frame
            import cv2
            import numpy as np

            _cache = {}

            def _cvat_getter(fid):
                if fid not in _cache:
                    try:
                        raw = get_job_frame(job_id, fid)
                        _cache[fid] = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None
                    except Exception:
                        _cache[fid] = None
                return _cache[fid]

            frame_getter = _cvat_getter
        except Exception as e:
            pass

    elif frame_getter is None and token:
        try:
            from studio.backend.video import manager
            import cv2
            import numpy as np

            _cache = {}

            def _video_getter(fid):
                if fid not in _cache:
                    try:
                        raw = manager.frame(token, fid)
                        _cache[fid] = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None
                    except Exception:
                        _cache[fid] = None
                return _cache[fid]

            frame_getter = _video_getter
        except Exception as e:
            pass

    if frame_getter is not None and body.get('use_reid', True):
        try:
            from core.reid import reid_filter_and_enhance_issues, find_additional_visual_merges

            issues = reid_filter_and_enhance_issues(issues, tracks, frame_getter, margin_threshold=0.05)

            if body.get('include_merges', True):
                existing_pairs = {(i.get('fix_data') or {}).get('pair') for i in issues if (i.get('fix_data') or {}).get('pair')}
                extra_merges = find_additional_visual_merges(
                    tracks, frame_getter, cfg, existing_pairs=existing_pairs, max_gap=15, min_visual_sim=0.75
                )
                for em in extra_merges:
                    issues.append(issue(
                        em['type'], em['frame'], em['track_id'], em['message'],
                        severity=em['severity'], suggestion=em['suggestion'], data=em['fix_data']
                    ))
        except Exception as e:
            pass


    # Phát hiện frame rỗng không có box trong video (mức LOW để đối soát)
    if mode == 'video' and total_frames is not None and rows:
        total = int(total_frames)
        if 0 < total <= 500_000:
            start = int(frame_base) if frame_base is not None else min(b['frame_id'] for b in rows)
            present = {b['frame_id'] for b in rows}
            missing = sorted(set(range(start, start + total)) - present)
            if missing:
                first = prev = missing[0]
                blocks = []
                for f_num in missing[1:]:
                    if f_num == prev + 1:
                        prev = f_num
                    else:
                        blocks.append((first, prev))
                        first = prev = f_num
                blocks.append((first, prev))
                for a, b_end in blocks[:100]:
                    frame_range_str = f"Frame {a}" if a == b_end else f"Frame {a}–{b_end}"
                    issues.append(issue('Empty Frame Candidate', a, -1,
                                        f'{frame_range_str} không có annotation nào (kiểm tra xem có vật thể bị sót không)', 'low'))

    sev_order = {'high': 0, 'medium': 1, 'low': 2}
    issues.sort(key=lambda i: (sev_order.get(i['severity'], 3), i['frame'], i['type']))
    ct = Counter(x['type'] for x in issues)
    allframes = sorted({b['frame_id'] for b in rows})
    return dict(
        issues=issues,
        counts=dict(ct),
        count=len(issues),
        stats={
            'boxes': len(rows),
            'tracks': len({b['track_id'] for b in rows}),
            'annotated_frames': len(allframes),
            'first_frame': allframes[0] if allframes else 0,
            'last_frame': allframes[-1] if allframes else 0,
            'total_frames': total_frames,
            'high': sum(i['severity'] == 'high' for i in issues),
            'medium': sum(i['severity'] == 'medium' for i in issues),
            'low': sum(i['severity'] == 'low' for i in issues)
        },
        notes=[
            'Được kiểm tra bằng bộ rule idcheck tối ưu (Hungarian matching, Kalman/velocity, camera compensation, spike filter, invalid box).',
            'Reviewer chỉ QC và tạo task bàn giao, Annotator sửa và lưu project.'
        ]
    )


def fix(body):
    boxes=apply_fix(body.get('boxes',[]),str(body.get('action','')),body.get('data',{}))
    if body.get('session'):
        audit.record(body['session'],'fix',{'type':body.get('action'),'parameters':body.get('data',{}),
                                          'before':len(body.get('boxes',[])),'after':len(boxes)})
    return {'boxes':boxes,'count':len(boxes)}


def review(body):
    entry=audit.record(body['session'],'review',{'issue_id':body.get('issue_id'),
                'decision':body.get('decision'),'note':str(body.get('note',''))[:400]})
    return {'saved':True,'event':entry}


def history(body):
    return {'events':audit.read(body['session'])}


def export(body):
    fmt=body.get('format','mot')
    boxes=normalize_boxes(body.get('boxes',[]))
    if not boxes:raise ValueError('Không có box để xuất')
    base=int(body.get('frame_base',1))
    if fmt=='mot':
        content,labels=export_mot(boxes,base,body.get('labels'))
        return {'filename':'t021_clean_mot.txt','content':content,'labels':labels,'format':'mot'}
    if fmt=='cvat':
        if body.get('mode')=='images' and any(b['track_id']<0 for b in boxes):
            raise ValueError('CVAT dạng ảnh không có track ID thực. Không thể xuất CVAT for video an toàn.')
        return {'filename':'t021_clean_cvat.xml','content':export_cvat(boxes,base,
                body.get('width') or 1920,body.get('height') or 1080,
                body.get('total_frames'),body.get('labels')),'format':'cvat'}
    if fmt=='json':
        return {'filename':'t021_clean_annotations.json','data':boxes,'format':'json'}
    raise ValueError('Chỉ hỗ trợ MOT, CVAT XML và JSON')


def quality(body):
    # Saved projects use authoritative server data and durable Reviewer decisions.
    if body.get('project_id'):
        from backend.projects import get
        project=get(body['project_id'])
        state=project.get('state') or {}
        values={**(state.get('meta') or {}), 'boxes':state.get('boxes') or []}
        tasks=project.get('tasks',{})
        reviews={issue_id:item.get('status') for issue_id,item in tasks.items()}
    else:
        values=body
        reviews={}  # browser-submitted decisions are never an authority
    result=scan(values)
    blocking=[i for i in result['issues'] if i['type'] in ('Invalid Box','Duplicate Track/Frame','Class Change')]
    open_high=[i for i in result['issues'] if i['severity']=='high' and reviews.get(i['id']) not in ('false_positive','verified')]
    pending=[issue_id for issue_id,status in reviews.items() if status in ('needs_fix','fixed')]
    return dict(pass_gate=not blocking and not open_high and not pending,structural_blockers=blocking,
                unresolved_high=open_high,pending_handoffs=pending,reviewed=len(reviews),total_issues=result['count'],
                disclaimer='Quality Gate dữ liệu; KHÔNG chứng nhận độ chính xác detector trên ground truth.')


def evaluate(body):
    from studio_core.evaluation.metrics import evaluate_events
    return evaluate_events(body.get('predicted',[]),body.get('ground_truth',[]),body.get('tolerance',2))


def video_info(body):
    from backend.video import VIDEO
    return VIDEO.metadata(body.get('token'))


def media_manifest(body):
    from backend.media import STORE, ZIP_ROOT, natural_sort_key
    import zipfile
    token=body.get('token')
    STORE.check_token(token)
    with zipfile.ZipFile(ZIP_ROOT / (token + '.zip')) as z:
        names=[info.filename.replace('\\','/').split('/')[-1] for info in z.infolist()
               if not info.is_dir() and info.filename.lower().endswith(('.jpg','.jpeg','.png','.webp','.gif'))]
    names.sort(key=natural_sort_key)
    return {'token':token,'files':names,'count':len(names)}


def demo(_):
    rows=[]
    for f in range(1,31):
        if f not in (14,15):
            rows.append(dict(frame_id=f,track_id=1 if f<=20 else 7,x=50+f*3,y=100,w=60,h=35,class_name='Car'))
        rows.append(dict(frame_id=f,track_id=2,x=430-f*4,y=210,w=70,h=42,class_name='Car'))
    # demonstrative bbox spike & duplicate data
    for r in rows:
        if r['frame_id']==9 and r['track_id']==2:r['x']+=160
    return dict(boxes=normalize_boxes(rows),format='mot',mode='video',frame_base=1,
                width=640,height=360,total_frames=30,fps=30.0,labels=['Car'],warnings=['Dữ liệu DEMO giả lập có gap, jump, và chuyển ID.'],
                session=audit.new_session())

ROUTES={'/api/parse':parse,'/api/validate':scan,'/api/fix':fix,'/api/review':review,
        '/api/audit':history,'/api/export':export,'/api/quality':quality,
        '/api/evaluate':evaluate,'/api/demo':demo,'/api/media/manifest':media_manifest,'/api/video/info':video_info}
