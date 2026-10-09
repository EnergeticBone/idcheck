import sys, glob, yaml
from math import hypot
from adapters.mot import load_mot_gt, load_fps
from core.config import scale_cfg
from core.inject import inject_many
from core.rules import center, size, check_switches

seq = sys.argv[1]
cfg = yaml.safe_load(open(sys.argv[2]))
gt = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)[0]
fps = load_fps(gt)
cfg = scale_cfg(cfg, fps)
p = cfg["default"]
clean = load_mot_gt(gt)
dirty, truths = inject_many(clean, n_per_type=10, seed=0, drop_range=(2, 25),
                            gap_range=(2, 12), jump_range=(1.0, 4.0))
found = {(e["track_id"], e["other_id"], e["frame"]) for e in check_switches(dirty, cfg)}


def d(u, v):
    return hypot(center(u)[0] - center(v)[0], center(u)[1] - center(v)[1])


def around(tr, t):
    fs = sorted(tr.boxes)
    prv = max(f for f in fs if f < t)
    nxt = min(f for f in fs if f >= t)
    return tr.boxes[prv], tr.boxes[nxt], nxt - prv


cnt = dict(total=0, hit=0, low_disp=0, bad_cross=0, other=0)
for t in truths:
    if t["type"] != "switch":
        continue
    cnt["total"] += 1
    a, b, f = t["a"], t["b"], t["frame"]
    pa, ca, sa = around(dirty[a], f)
    pb, cb, sb = around(dirty[b], f)
    da = d(pa, ca) / size(pa) / sa
    db = d(pb, cb) / size(pb) / sb
    straight = d(pa, ca) + d(pb, cb)
    cross = (d(pa, cb) + d(pb, ca)) / straight if straight else 9
    hit = any(abs(fr - f) <= 3 and {x, y} == {a, b} for x, y, fr in found)
    if hit:
        cnt["hit"] += 1
        continue
    if min(da, db) < p["swap_min_disp"]:
        cnt["low_disp"] += 1
    elif cross > p["swap_cross_ratio"]:
        cnt["bad_cross"] += 1
    else:
        cnt["other"] += 1
    print(f"MISS {a}<->{b} f{f}: disp {da:.2f}/{db:.2f}, cross/straight {cross:.2f}, "
          f"cỡ box {size(pa):.0f}/{size(pb):.0f}")
print(cnt)