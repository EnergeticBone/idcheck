from collections import Counter
from math import hypot


def center(b):
    return ((b.x1 + b.x2) / 2, (b.y1 + b.y2) / 2)


def size(b):
    return max(b.x2 - b.x1, b.y2 - b.y1, 1e-6)


def iou(a, b):
    iw = min(a.x2, b.x2) - max(a.x1, b.x1)
    ih = min(a.y2, b.y2) - max(a.y1, b.y1)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    ua = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter
    return inter / ua if ua > 0 else 0.0


def params(cfg, label):
    p = dict(cfg.get("default", {}))
    p.update((cfg.get("classes") or {}).get(label) or {})
    return p


def check_gaps(tr, cfg):
    p, errs = params(cfg, tr.label), []
    fs = sorted(tr.boxes)
    for a, b in zip(fs, fs[1:]):
        missing = b - a - 1
        if not (p["min_missing_gap"] <= missing <= p["max_missing_gap"]):
            continue
        A, B = tr.boxes[a], tr.boxes[b]
        if A.outside or A.occluded or B.occluded:    # che khuất thật, không phải lỗi
            continue
        errs.append(dict(type="gap", track_id=tr.id, frame=a,
                         detail=f"thiếu {missing} frame"))
    return errs


def check_jumps(tr, cfg):
    p, errs, last = params(cfg, tr.label), [], -10**9
    fs = sorted(f for f, b in tr.boxes.items() if not b.outside)
    for a, b in zip(fs, fs[1:]):
        if b - a > p["max_frame_step"]:
            continue
        A, B = tr.boxes[a], tr.boxes[b]
        (xa, ya), (xb, yb) = center(A), center(B)
        disp = hypot(xb - xa, yb - ya) / size(A) / (b - a)
        ratio = size(B) / size(A)
        bad_scale = not (1 / p["max_scale"] < ratio < p["max_scale"])
        if disp > p["max_disp_ratio"] or bad_scale:
            if b - last <= 2:          # box nhảy đi rồi nhảy về: chỉ báo một lần
                continue
            last = b
            errs.append(dict(type="jump", track_id=tr.id, frame=b,
                             detail=f"dịch {disp:.2f} lần kích thước/frame, đổi cỡ x{ratio:.2f}"))
    return errs


def check_class_flips(tr, cfg):
    fs = sorted(tr.boxes)
    major = Counter(tr.boxes[f].label for f in fs).most_common(1)[0][0]
    errs, prev_bad = [], False
    for f in fs:
        bad = tr.boxes[f].label != major
        if bad and not prev_bad:
            errs.append(dict(type="class_flip", track_id=tr.id, frame=f,
                             detail=f"{tr.boxes[f].label} thay vì {major}"))
        prev_bad = bad
    return errs


def check_duplicates(tracks, cfg):
    p, errs = cfg.get("default", {}), []
    ids = sorted(tracks)
    for i, a in enumerate(ids):
        A = tracks[a]
        for b in ids[i + 1:]:
            B = tracks[b]
            common = sorted(set(A.boxes) & set(B.boxes))
            if len(common) < p["dup_min_frames"]:
                continue
            hits = [f for f in common if iou(A.boxes[f], B.boxes[f]) > p["dup_iou"]]
            if len(hits) >= p["dup_min_frames"]:
                errs.append(dict(type="duplicate", track_id=a, other_id=b, frame=hits[0],
                                 detail=f"trùng {len(hits)} frame với track {b}"))
    return errs


def run_rules(tracks, cfg):
    errs = []
    for tr in tracks.values():
        errs += check_gaps(tr, cfg)
        errs += check_jumps(tr, cfg)
        errs += check_class_flips(tr, cfg)
    errs += check_duplicates(tracks, cfg)
    return sorted(errs, key=lambda e: (e["track_id"], e["frame"]))