import sys, glob, yaml, copy
from adapters.mot import load_mot_gt, load_fps
from core.config import scale_cfg
from core.inject import inject_many
from core.rules import check_gaps

seq, n = sys.argv[1], int(sys.argv[2])
gt = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)[0]
cfg = yaml.safe_load(open(sys.argv[3] if len(sys.argv) > 3 else "config.yaml"))
fps = load_fps(gt)
cfg = scale_cfg(cfg, fps)
clean = load_mot_gt(gt)
dirty, truths = inject_many(clean, n_per_type=n, seed=42)
want = {(t["a"], t["frame"]) for t in truths if t["type"] == "gap"}

for skip in (True, False):
    c = copy.deepcopy(cfg)
    c["default"]["skip_occluded_gap"] = skip
    fp = sum(len(check_gaps(tr, c)) for tr in clean.values())
    got = {(e["track_id"], e["frame"]) for tr in dirty.values() for e in check_gaps(tr, c)}
    print(f"skip_occluded_gap={skip}: cảnh báo trên SẠCH={fp}, "
          f"bắt được {len(want & got)}/{len(want)}")