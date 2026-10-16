# core/merge.py
from math import exp, hypot
import numpy as np
from scipy.optimize import linear_sum_assignment
from core.rules import center, size, params

BIG = 1e6
DEFAULTS = dict(max_merge_gap=30, max_merge_dist=2.0, max_merge_scale=2.0, merge_max_cost=3.5, min_merge_track_len=4)


def _p(cfg, label):
    return {**DEFAULTS, **params(cfg, label)}


def predict(tr, gap, k=5):
    fs = sorted(f for f, b in tr.boxes.items() if not b.outside)[-k:]
    if not fs:
        fs = sorted(tr.boxes)[-k:]
    cs = [center(tr.boxes[f]) for f in fs]
    if len(cs) < 2:
        return cs[-1]
    vx = (cs[-1][0] - cs[0][0]) / (fs[-1] - fs[0])
    vy = (cs[-1][1] - cs[0][1]) / (fs[-1] - fs[0])
    return (cs[-1][0] + vx * gap, cs[-1][1] + vy * gap)

def _border_sides(b, size, m):
    W, H = size
    s = set()
    if b.x1 <= m: s.add("L")
    if b.x2 >= W - m: s.add("R")
    if b.y1 <= m: s.add("T")
    if b.y2 >= H - m: s.add("B")
    return s

def _vel(tr, k=5, tail=True):
    fs = sorted(f for f, b in tr.boxes.items() if not b.outside)
    fs = fs[-k:] if tail else fs[:k]
    if len(fs) < 2:
        return 0.0, 0.0
    (x0, y0), (x1, y1) = center(tr.boxes[fs[0]]), center(tr.boxes[fs[-1]])
    return (x1 - x0) / (fs[-1] - fs[0]), (y1 - y0) / (fs[-1] - fs[0])


def _moving_through(box, v, fsz, m, outward, eps=0.3):
    vx, vy = v
    s_out = {"L": vx < -eps, "R": vx > eps, "T": vy < -eps, "B": vy > eps}
    s_in = {"L": vx > eps, "R": vx < -eps, "T": vy > eps, "B": vy < -eps}
    table = s_out if outward else s_in
    return {s for s in _border_sides(box, fsz, m) if table[s]}

def suggest_merges(tracks, cfg, min_score=0.0, frame_size=None):
    ids = list(tracks)
    n = len(ids)
    if n == 0:
        return []
    cost = np.full((n, n), BIG)
    geo = np.full((n, n), BIG)
    for i, a in enumerate(ids):
        A = tracks[a]
        p = _p(cfg, A.label)
        min_track_len = p.get("min_merge_track_len", 3)
        vis_a = sum(1 for x in A.boxes.values() if not x.outside)
        if vis_a < min_track_len:
            continue
        a_end = A.boxes[A.end]
        for j, b in enumerate(ids):
            B = tracks[b]
            vis_b = sum(1 for x in B.boxes.values() if not x.outside)
            if vis_b < min_track_len:
                continue
            gap = B.start - A.end
            if a == b or A.label != B.label or not (1 <= gap <= p["max_merge_gap"]):
                continue
            b_start = B.boxes[B.start]
            ratio = size(b_start) / size(a_end)
            if not (1 / p["max_merge_scale"] < ratio < p["max_merge_scale"]):
                continue
            px, py = predict(A, gap)
            bx, by = center(b_start)
            dist = hypot(px - bx, py - by) / size(a_end)
            if dist >= p["max_merge_dist"]:
                continue
            g = dist
            m = p.get("border_margin", 0)
            vA, vB = _vel(A, tail=True), _vel(B, tail=False)
            if frame_size is not None and m > 0:
                both_border = _border_sides(a_end, frame_size, m) & _border_sides(b_start, frame_size, m)
                if both_border:
                    g += p.get("border_penalty", 2.0)      # A kết thúc và B bắt đầu ở cùng mép
                elif _moving_through(a_end, vA, frame_size, m, True) & \
                   _moving_through(b_start, vB, frame_size, m, False):
                    g += p.get("border_penalty", 2.0)      # A đi ra, B đi vào cùng mép
            sa, sb = hypot(*vA), hypot(*vB)
            if sa > 0.3 and sb > 0.3:
                cos = (vA[0] * vB[0] + vA[1] * vB[1]) / (sa * sb)
                g += p.get("merge_dir_weight", 0.5) * (1 - cos) / 2
            geo[i, j] = g                  # dùng cho score
            cost[i, j] = g + p.get("merge_gap_cost", 0.05) * gap    # dùng cho ghép cặp

    reject = 0.5 * _p(cfg, None)["merge_max_cost"]
    M = np.full((2 * n, 2 * n), BIG)
    M[:n, :n] = cost
    M[n:, n:] = 0.0
    idx = np.arange(n)
    M[idx, n + idx] = reject
    M[n + idx, idx] = reject

    rows, cols = linear_sum_assignment(M)
    out = []
    for r, c in zip(rows, cols):
        if r < n and c < n and cost[r, c] < BIG:
            s = round(exp(-geo[r, c]), 3)
            if s >= min_score:
                out.append(dict(a=ids[r], b=ids[c], score=s,
                                gap=tracks[ids[c]].start - tracks[ids[r]].end))
    return sorted(out, key=lambda x: (-x["score"], x["gap"]))