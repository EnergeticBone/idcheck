from collections import Counter
from math import hypot
from statistics import median


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


def check_invalid_boxes(tr, cfg, frame_size=None):
    """Kiểm tra tính toàn vẹn hình học của các box trong track:
    1. Kích thước bất thường: width <= 0 hoặc height <= 0 (bẹp hoặc kéo ngược tọa độ)
    2. Văng ra ngoài khung hình: box nhìn thấy nhưng nằm hoàn toàn ngoài phạm vi canvas
    """
    p = params(cfg, tr.label)
    fsz = frame_size or p.get("frame_size")
    errs = []
    for f, b in tr.boxes.items():
        if b.outside:
            continue
        w = b.x2 - b.x1
        h = b.y2 - b.y1
        if w <= 0 or h <= 0:
            errs.append(dict(
                type="invalid_box",
                track_id=tr.id,
                frame=f,
                detail=f"Kích thước box không hợp lệ: w={w:.1f}, h={h:.1f} (chiều rộng/cao <= 0)"
            ))
            continue
        if fsz:
            W, H = fsz
            if b.x2 <= 0 or b.y2 <= 0 or b.x1 >= W or b.y1 >= H:
                errs.append(dict(
                    type="invalid_box",
                    track_id=tr.id,
                    frame=f,
                    detail=f"Box nằm hoàn toàn ngoài khung hình ({W}x{H}): [{b.x1:.1f}, {b.y1:.1f}, {b.x2:.1f}, {b.y2:.1f}]"
                ))
    return errs


def check_gaps(tr, cfg):
    p, errs = params(cfg, tr.label), []
    skip_occ = p.get("skip_occluded_gap", True)
    vis = sorted(f for f, b in tr.boxes.items() if not b.outside)
    for a, b in zip(vis, vis[1:]):
        missing = b - a - 1
        if not (p["min_missing_gap"] <= missing <= p["max_missing_gap"]):
            continue
        # Bỏ qua nếu annotator chủ động đánh dấu outside ở giữa khoảng gap
        if any(f in tr.boxes and tr.boxes[f].outside for f in range(a + 1, b)):
            continue
        A, B = tr.boxes[a], tr.boxes[b]
        if skip_occ and (A.occluded or B.occluded):
            continue
        errs.append(dict(type="gap", track_id=tr.id, frame=a,
                         detail=f"thiếu {missing} frame"))
    return errs


def build_center_index(tracks):
    """frame -> {track_id: tâm box}, dùng để ước lượng chuyển động chung của cảnh."""
    idx = {}
    for tr in tracks.values():
        for f, b in tr.boxes.items():
            if not b.outside:
                idx.setdefault(f, {})[tr.id] = center(b)
    return idx


def _others_shift(index, exclude_id, a, b, min_tracks):
    ca, cb = index.get(a, {}), index.get(b, {})
    common = [t for t in ca.keys() & cb.keys() if t != exclude_id]
    if len(common) < min_tracks:
        return None
    return (median(cb[t][0] - ca[t][0] for t in common),
            median(cb[t][1] - ca[t][1] for t in common))


def check_jumps(tr, cfg, index=None):
    p, errs, last = params(cfg, tr.label), [], -10**9
    skip_spike_frame = -10**9
    skip_occ_scale = p.get("skip_occluded_scale", True)
    min_box_size = p.get("min_box_size", 30.0)
    m = p.get("border_margin", 0)
    frame_size = p.get("frame_size", (1280, 720))
    max_disp = p.get("max_disp_ratio", 3.0)
    entry_mult = p.get("entry_disp_mult", 1.6)

    def _touching_border(b):
        if m <= 0 or not frame_size:
            return False
        W, H = frame_size
        return b.x1 <= m or b.x2 >= W - m or b.y1 <= m or b.y2 >= H - m

    fs = sorted(f for f, b in tr.boxes.items() if not b.outside)
    for i, (a, b) in enumerate(zip(fs, fs[1:])):
        if b - a > p["max_frame_step"]:
            continue
        A, B = tr.boxes[a], tr.boxes[b]
        (xa, ya), (xb, yb) = center(A), center(B)
        eff_size = max(size(A), min_box_size)
        disp = hypot(xb - xa, yb - ya) / eff_size / (b - a)
        ratio = size(B) / size(A)

        # 1. Bỏ qua đổi cỡ nếu bị che khuất hoặc xe đang đi ra/vào từ mép khung hình
        if (skip_occ_scale and (A.occluded or B.occluded)) or _touching_border(A) or _touching_border(B):
            bad_scale = False
        else:
            bad_scale = not (1 / p["max_scale"] < ratio < p["max_scale"])

        # Nới lỏng nhẹ ngưỡng dịch chuyển ở bước đầu tiên khi đối tượng mới trượt vào tầm nhìn
        effective_max_disp = max_disp * (entry_mult if i == 0 else 1.0)

        # 2. Kiểm tra tính liên tục theo hướng (quán tính chuyển động mượt khi xe trượt vào)
        is_smooth_drift = False
        if disp > effective_max_disp and not bad_scale and i + 2 < len(fs) and disp <= effective_max_disp * 1.5:
            c = fs[i + 2]
            if c - b <= p["max_frame_step"]:
                C = tr.boxes[c]
                (xc, yc) = center(C)
                v1 = ((xb - xa) / (b - a), (yb - ya) / (b - a))
                v2 = ((xc - xb) / (c - b), (yc - yb) / (c - b))
                dot = v1[0] * v2[0] + v1[1] * v2[1]
                s1, s2 = hypot(*v1), hypot(*v2)
                if s1 > 0 and s2 > 0 and (dot / (s1 * s2) > 0.5) and (s2 >= 0.25 * s1):
                    is_smooth_drift = True

        if (disp > effective_max_disp or bad_scale) and not is_smooth_drift:
            if (p.get("compensate_camera", False) and index is not None
                    and not bad_scale):
                g = _others_shift(index, tr.id, a, b, p.get("cam_min_tracks", 3))
                if g is not None:
                    comp = hypot(xb - xa - g[0], yb - ya - g[1]) / eff_size / (b - a)
                    if comp <= max_disp:
                        continue          # cả cảnh dịch cùng nhau: camera rẽ cua/đánh lái

            if b == skip_spike_frame:
                continue

            # Kiểm tra nếu đây là lỗi Spike (box lệch ở b rồi ngay frame c kế tiếp quay lại quỹ đạo của a)
            is_spike = False
            next_skip = None
            if i + 2 < len(fs):
                c = fs[i + 2]
                if c - b <= p["max_frame_step"]:
                    C = tr.boxes[c]
                    (xc, yc) = center(C)
                    disp_ac = hypot(xc - xa, yc - ya) / max(size(A), min_box_size) / (c - a)
                    ratio_ac = size(C) / size(A) if size(A) > 0 else 1.0
                    scale_ac_ok = (1 / p["max_scale"] < ratio_ac < p["max_scale"]) or (skip_occ_scale and (A.occluded or C.occluded))
                    if disp_ac <= effective_max_disp and scale_ac_ok:
                        disp_bc = hypot(xc - xb, yc - yb) / max(size(B), min_box_size) / (c - b)
                        ratio_bc = size(C) / size(B) if size(B) > 0 else 1.0
                        bad_scale_bc = not (1 / p["max_scale"] < ratio_bc < p["max_scale"])
                        if disp_bc > effective_max_disp or bad_scale_bc:
                            is_spike = True
                            next_skip = c

            if b - last <= 2 and not is_spike:
                continue
            last = b
            if is_spike:
                skip_spike_frame = next_skip
                errs.append(dict(type="jump", track_id=tr.id, frame=b, subtype="spike",
                                 detail=f"Spike: Box lệch ở frame {b} rồi quay lại quỹ đạo ở frame {next_skip} (dịch {disp:.2f}x, cỡ x{ratio:.2f})"))
            else:
                errs.append(dict(type="jump", track_id=tr.id, frame=b,
                                 detail=f"dịch {disp:.2f} lần kích thước/frame, đổi cỡ x{ratio:.2f}"))
    return errs


def check_class_flips(tr, cfg):
    p = cfg.get("default", {})
    if not p.get("enable_class_flips", True):
        return []
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


def run_rules(tracks, cfg, frame_size=None):
    errs = []
    index = build_center_index(tracks)
    for tr in tracks.values():
        errs += check_invalid_boxes(tr, cfg, frame_size)
        errs += check_gaps(tr, cfg)
        errs += check_jumps(tr, cfg, index)
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