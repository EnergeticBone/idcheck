"""Normalized annotation data and geometry helpers. Internal frame_id always source-relative."""
import math

FIELDS = ('uid', 'frame_id', 'track_id', 'x', 'y', 'w', 'h', 'class_name')


def normalize_boxes(rows):
    if not isinstance(rows, list):
        raise ValueError('boxes phải là một danh sách.')
    if len(rows) > 250_000:
        raise ValueError('Tối đa 250.000 bounding boxes mỗi lần.')
    result = []
    seen = set()
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ValueError(f'Box thứ {i+1} không hợp lệ.')
        try:
            frame, track = int(r['frame_id']), int(r['track_id'])
            x, y, w, h = (float(r[k]) for k in ('x', 'y', 'w', 'h'))
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f'Box thứ {i+1} thiếu trường hoặc có số không hợp lệ.') from exc
        if not all(math.isfinite(v) for v in (x, y, w, h)):
            raise ValueError(f'Box thứ {i+1} chứa NaN/Infinity.')
        uid = str(r.get('uid') or f'b{i+1}')
        if uid in seen:
            uid = f'b{i+1}_{i}'
        seen.add(uid)
        result.append(dict(uid=uid, frame_id=frame, track_id=track, x=x, y=y, w=w, h=h,
                           class_name=str(r.get('class_name') or 'Car'),
                           occluded=bool(r.get('occluded', False)),
                           keyframe=bool(r.get('keyframe', True))))
    return result


def center(b):
    return (b['x'] + b['w'] / 2, b['y'] + b['h'] / 2)


def iou(a, b):
    x1, y1 = max(a['x'], b['x']), max(a['y'], b['y'])
    x2, y2 = min(a['x'] + max(a['w'], 0), b['x'] + max(b['w'], 0)), min(a['y'] + max(a['h'], 0), b['y'] + max(b['h'], 0))
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = max(a['w'], 0) * max(a['h'], 0) + max(b['w'], 0) * max(b['h'], 0) - inter
    return inter / union if union > 0 else 0


def distance(a, b):
    ax, ay = center(a); bx, by = center(b)
    return math.hypot(ax-bx, ay-by)


def issue(kind, frame, track, message, severity='medium', suggestion='review', data=None, confidence=None):
    # Stable ID across scans; client can keep review decisions until data changes.
    tag = str(data.get('pair') if isinstance(data, dict) and 'pair' in data else '')
    key = f'{kind}:{frame}:{track}:{tag}'
    out = dict(id=key, type=kind, frame=int(frame), track_id=int(track), message=message,
               severity=severity, suggestion=suggestion, fix_data=data or {})
    if confidence is not None: out['confidence'] = round(confidence, 3)
    return out
