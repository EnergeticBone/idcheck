import sys, glob, yaml, copy
from adapters.mot import load_mot_gt, load_fps
from core.config import scale_cfg
from core.inject import inject_many
from core.rules import check_jumps

cfg = yaml.safe_load(open(sys.argv[1]))
seqs = yaml.safe_load(open("splits.yaml"))["tune"]
data = []
for seq in seqs:
    f = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)
    if not f:
        continue
    clean, fps = load_mot_gt(f[0]), load_fps(f[0])
    k = fps / 30.0
    dirty, truths = inject_many(
        clean, n_per_type=10, seed=0,
        drop_range=(max(2, round(2 * k)), max(3, round(25 * k))),
        gap_range=(max(2, round(2 * k)), max(3, round(12 * k))),
        jump_range=(1.0, 4.0))
    want = {(t["a"], t["frame"]) for t in truths if t["type"] == "jump"}
    data.append((clean, dirty, want, fps))

print("max_disp_ratio | cảnh báo trên SẠCH | recall jump")
for v in (1.5, 1.0, 0.8, 0.6, 0.5, 0.4, 0.3):
    c0 = copy.deepcopy(cfg)
    c0["default"]["max_disp_ratio"] = v
    for cl in (c0.get("classes") or {}).values():
        if cl:
            cl.pop("max_disp_ratio", None)
    fp = hit = tot = 0
    for clean, dirty, want, fps in data:
        c = scale_cfg(c0, fps)
        fp += sum(len(check_jumps(tr, c)) for tr in clean.values())
        got = {(e["track_id"], e["frame"]) for tr in dirty.values() for e in check_jumps(tr, c)}
        tot += len(want)
        hit += len(want & got)
    print(f"{v:<15}| {fp:<19}| {hit}/{tot}")
