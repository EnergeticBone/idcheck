# core/merge.py
from math import exp, hypot
import numpy as np
from scipy.optimize import linear_sum_assignment
from core.rules import center, size, params

BIG = 1e6
DEFAULTS = dict(max_merge_gap=30, max_merge_dist=2.0, max_merge_scale=2.0)


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


def suggest_merges(tracks, cfg, min_score=0.0):
    ids = list(tracks)
    n = len(ids)
    if n == 0:
        return []
    cost = np.full((n, n), BIG)
    for i, a in enumerate(ids):
        A = tracks[a]
        p = _p(cfg, A.label)
        a_end = A.boxes[A.end]
        for j, b in enumerate(ids):
            B = tracks[b]
            gap = B.start - A.end
            if a == b or A.label != B.label or not (1 <= gap <= p["max_merge_gap"]):
                continue
            b_start = B.boxes[B.start]
            ratio = size(b_start) / size(a_end)          # kích thước phải tương đương
            if not (1 / p["max_merge_scale"] < ratio < p["max_merge_scale"]):
                continue
            px, py = predict(A, gap)
            bx, by = center(b_start)
            dist = hypot(px - bx, py - by) / size(a_end)
            if dist < p["max_merge_dist"]:
                cost[i, j] = dist + 0.05 * gap

    # Hàng/cột "không nối": một cặp chỉ được ghép khi cost < 2*reject
    d = _p(cfg, None)
    reject = 0.5 * (d["max_merge_dist"] + 0.05 * d["max_merge_gap"])
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
            s = round(exp(-cost[r, c]), 3)
            if s >= min_score:
                out.append(dict(a=ids[r], b=ids[c], score=s))
    return sorted(out, key=lambda x: x["score"], reverse=True)
