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
    skip_occ = p.get("skip_occluded_gap", True)
    fs = sorted(tr.boxes)
    for a, b in zip(fs, fs[1:]):
        missing = b - a - 1
        if not (p["min_missing_gap"] <= missing <= p["max_missing_gap"]):
            continue
        A, B = tr.boxes[a], tr.boxes[b]
        if A.outside:                                  # rời khung hình hợp lệ
            continue
        if skip_occ and (A.occluded or B.occluded):    # che khuất: tuỳ chọn
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


def _longest_run(frames):
    best = run = 0
    prev = None
    for f in frames:
        run = run + 1 if prev is not None and f == prev + 1 else 1
        best = max(best, run)
        prev = f
    return best


def check_duplicates(tracks, cfg):
    p, errs = cfg.get("default", {}), []
    min_ratio = p.get("dup_min_ratio", 0.6)
    ids = sorted(tracks)
    for i, a in enumerate(ids):
        A = tracks[a]
        for b in ids[i + 1:]:
            B = tracks[b]
            common = sorted(set(A.boxes) & set(B.boxes))
            if len(common) < p["dup_min_frames"]:
                continue
            hits = [f for f in common if iou(A.boxes[f], B.boxes[f]) > p["dup_iou"]]
            if (len(hits) >= p["dup_min_frames"]
                    and len(hits) / len(common) >= min_ratio
                    and _longest_run(hits) >= p["dup_min_frames"]):
                errs.append(dict(type="duplicate", track_id=a, other_id=b, frame=hits[0],
                                 detail=f"trùng {len(hits)}/{len(common)} frame với track {b}"))
    return errs


def run_rules(tracks, cfg):
    errs = []
    for tr in tracks.values():
        errs += check_gaps(tr, cfg)
        errs += check_jumps(tr, cfg)
        errs += check_class_flips(tr, cfg)
    errs += check_duplicates(tracks, cfg)
    errs += check_switches(tracks, cfg)
    return sorted(errs, key=lambda e: (e["track_id"], e["frame"]))

def check_switches(tracks, cfg):
    p = cfg.get("default", {})
    min_disp = p.get("swap_min_disp", 0.0)    # 0 = không dùng cổng bước nhảy
    min_sep = p.get("swap_min_sep", 0.3)      # khoảng cách tối thiểu giữa hai track (theo cỡ box)
    max_sep = p.get("swap_max_sep", 3.0)      # chỉ xét các cặp ở gần nhau
    ratio = p.get("swap_cross_ratio", 0.5)
    step = p.get("max_frame_step", 3)

    by_frame = {}
    for tid in sorted(tracks):
        tr = tracks[tid]
        fs = sorted(f for f, b in tr.boxes.items() if not b.outside)
        for a, b in zip(fs, fs[1:]):
            if b - a > step:
                continue
            A, B = tr.boxes[a], tr.boxes[b]
            if min_disp > 0:
                (xa, ya), (xb, yb) = center(A), center(B)
                if hypot(xb - xa, yb - ya) / size(A) / (b - a) < min_disp:
                    continue
            by_frame.setdefault(b, []).append((tid, A, B))

    def d(u, v):
        return hypot(center(u)[0] - center(v)[0], center(u)[1] - center(v)[1])

    errs = []
    for f, cands in by_frame.items():
        for i in range(len(cands)):
            ta, pa, ca = cands[i]
            for j in range(i + 1, len(cands)):
                tb, pb, cb = cands[j]
                if tracks[ta].label != tracks[tb].label:
                    continue
                sep = d(pa, pb) / ((size(pa) + size(pb)) / 2)
                if not (min_sep <= sep <= max_sep):
                    continue
                if d(pa, cb) + d(pb, ca) <= ratio * (d(pa, ca) + d(pb, cb)):
                    errs.append(dict(type="switch", track_id=ta, other_id=tb, frame=f,
                                     detail=f"nghi hoán ID giữa track {ta} và {tb}"))
    return errs