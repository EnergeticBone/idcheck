import sys, glob, yaml, copy
from adapters.mot import load_mot_gt, load_fps
from core.config import scale_cfg
from core.inject import inject_many
from core.rules import check_switches

cfg = yaml.safe_load(open(sys.argv[1]))
seqs = yaml.safe_load(open("splits.yaml"))["tune"]
data = []
for seq in seqs:
    f = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)
    if not f:
        continue
    fps = load_fps(f[0])
    k = fps / 30.0
    clean = load_mot_gt(f[0])
    dirty, truths = inject_many(clean, n_per_type=10, seed=0,
                                drop_range=(max(2, round(2 * k)), max(3, round(25 * k))),
                                gap_range=(max(2, round(2 * k)), max(3, round(12 * k))),
                                jump_range=(1.0, 4.0))
    data.append((clean, dirty, [t for t in truths if t["type"] == "switch"], fps))

print("min_sep  cross  | cảnh báo trên SẠCH | recall switch")
for sep in (0.1, 0.2, 0.3, 0.5):
    for cr in (0.3, 0.5, 0.7):
        c = copy.deepcopy(cfg)
        c["default"]["swap_min_sep"], c["default"]["swap_cross_ratio"] = sep, cr
        fp, hit, tot = 0, 0, 0
        for clean, dirty, sw, fps in data:
            c_s = scale_cfg(c, fps)
            fp += len(check_switches(clean, c_s))
            got = {(frozenset((e["track_id"], e["other_id"])), e["frame"]) for e in check_switches(dirty, c_s)}
            for t in sw:
                tot += 1
                if any(frozenset((t["a"], t["b"])) == kk and abs(fr - t["frame"]) <= 3 for kk, fr in got):
                    hit += 1
        print(f"{sep:<8}{cr:<7}| {fp:<19}| {hit}/{tot}")
