# core/inject.py
import copy
import random
from math import hypot

from core.schema import Box, Track


def _center(b):
    return ((b.x1 + b.x2) / 2, (b.y1 + b.y2) / 2)


def _size(b):
    return max(b.x2 - b.x1, b.y2 - b.y1)


def _new_id(tracks):
    return max(tracks) + 1


def split_track(tracks, tid, at, drop=3):
    """Lỗi fragment: cắt track tại `at`, bỏ `drop` frame, phần sau mang ID mới.
    truth.frame = frame cuối của đoạn đầu."""
    tr = tracks[tid]
    fs = sorted(tr.boxes)
    head = {f: tr.boxes[f] for f in fs if f < at}
    tail = {f: tr.boxes[f] for f in fs if f >= at + drop}
    if not head or not tail:
        return None
    nid = _new_id(tracks)
    tr.boxes = head
    tracks[nid] = Track(nid, tr.label, tail)
    return dict(type="fragment", a=tid, b=nid, frame=max(head))


def delete_frames(tracks, tid, at, k=3):
    """Lỗi gap: xoá k frame từ `at`. truth.frame = frame cuối trước chỗ đứt."""
    tr = tracks[tid]
    before = [f for f in tr.boxes if f < at]
    after = [f for f in tr.boxes if f >= at + k]
    if not before or not after:
        return None
    for f in range(at, at + k):
        tr.boxes.pop(f, None)
    return dict(type="gap", a=tid, b=None, frame=max(before))


def jump_box(tracks, tid, frame, ratio=3.0):
    """Lỗi jump: dịch một box ngang đi `ratio` lần kích thước của nó."""
    b = tracks[tid].boxes.get(frame)
    if b is None:
        return None
    d = ratio * _size(b)
    b.x1 += d
    b.x2 += d
    return dict(type="jump", a=tid, b=None, frame=frame)


def swap_ids(tracks, a, b, t):
    """Lỗi switch: hoán phần đuôi (từ frame t) của hai track."""
    A, B = tracks[a], tracks[b]
    ha = {f: x for f, x in A.boxes.items() if f < t}
    hb = {f: x for f, x in B.boxes.items() if f < t}
    ta = {f: x for f, x in A.boxes.items() if f >= t}
    tb = {f: x for f, x in B.boxes.items() if f >= t}
    if not (ha and hb and ta and tb):
        return None
    A.boxes = {**ha, **tb}
    B.boxes = {**hb, **ta}
    return dict(type="switch", a=a, b=b, frame=t)


def flip_class(tracks, tid, at, k, new_label):
    """Lỗi class_flip: đổi label của k box liên tiếp từ `at`."""
    tr = tracks[tid]
    hit = [f for f in sorted(tr.boxes) if at <= f < at + k]
    if not hit:
        return None
    for f in hit:
        tr.boxes[f].label = new_label
    return dict(type="class_flip", a=tid, b=None, frame=hit[0])


def duplicate_track(tracks, tid, shift=3.0):
    """Lỗi duplicate: sao chép track với ID mới, lệch vài pixel."""
    tr = tracks[tid]
    nid = _new_id(tracks)
    boxes = {}
    for f, b in tr.boxes.items():
        nb = copy.copy(b)
        nb.x1 += shift
        nb.x2 += shift
        boxes[f] = nb
    tracks[nid] = Track(nid, tr.label, boxes)
    return dict(type="duplicate", a=tid, b=nid, frame=tr.start)


def _nearest_peer(tracks, tid, t, used):
    """Track khác gần nhất tại frame t (để switch trông thật)."""
    ref = tracks[tid].boxes.get(t)
    if ref is None:
        return None
    best, bd = None, 1e18
    for oid, o in tracks.items():
        if oid == tid or oid in used or o.label != tracks[tid].label:
            continue
        ob = o.boxes.get(t)
        if ob is None or not any(f < t for f in o.boxes):
            continue
        (x1, y1), (x2, y2) = _center(ref), _center(ob)
        d = hypot(x1 - x2, y1 - y2)
        if d < bd:
            best, bd = oid, d
    return best


def inject_many(clean, n_per_type=10, seed=0, min_len=30, margin=10):
    """Chèn lỗi xen kẽ giữa các loại để không loại nào bị bỏ đói.
    Mỗi track chỉ bị chèn tối đa một lỗi."""
    rng = random.Random(seed)
    tracks = copy.deepcopy(clean)
    pool = [t for t, tr in tracks.items() if len(tr.boxes) >= min_len]
    rng.shuffle(pool)
    used, truths = set(), []
    kinds = ["fragment", "gap", "jump", "switch", "class_flip", "duplicate"]

    def try_inject(kind, tid):
        fs = sorted(tracks[tid].boxes)
        lo, hi = fs[0] + margin, fs[-1] - margin - 5
        if hi <= lo:
            return None
        target = rng.randint(lo, hi)
        at = min(fs, key=lambda f: abs(f - target))      # frame có thật
        if kind == "fragment":
            return split_track(tracks, tid, at, drop=rng.randint(2, 6))
        if kind == "gap":
            return delete_frames(tracks, tid, at, k=rng.randint(2, 6))
        if kind == "jump":
            return jump_box(tracks, tid, at, ratio=rng.uniform(2.0, 4.0))
        if kind == "switch":
            peer = _nearest_peer(tracks, tid, at, used)
            return swap_ids(tracks, tid, peer, at) if peer is not None else None
        if kind == "class_flip":
            return flip_class(tracks, tid, at, rng.randint(3, 8), "car")
        return duplicate_track(tracks, tid, shift=rng.uniform(1, 5))

    for _ in range(n_per_type):
        for kind in kinds:
            for tid in pool:
                if tid in used:
                    continue
                truth = try_inject(kind, tid)
                if truth:
                    used.update(x for x in (truth["a"], truth["b"]) if x is not None)
                    truths.append(truth)
                    break
    return tracks, truths