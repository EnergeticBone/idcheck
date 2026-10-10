import sys, glob, yaml
from collections import Counter
from adapters.mot import load_mot_gt, load_seqinfo, load_fps
from core.config import scale_cfg
from core.inject import inject_many
from core.evaluate import evaluate_merge

cfg = yaml.safe_load(open(sys.argv[1]))
split = sys.argv[2] if len(sys.argv) > 2 else "report"
print(f"{'chuỗi':18}{'fps':>5}{'track':>7}{'truth':>7}{'tp':>5}{'recall':>8}   gap>=13")
for seq in yaml.safe_load(open("splits.yaml"))[split]:
    f = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)
    if not f:
        continue
    clean, size, fps = load_mot_gt(f[0]), load_seqinfo(f[0]), load_fps(f[0])
    c, k = scale_cfg(cfg, fps), fps / 30.0
    tot, big = Counter(), [0, 0]
    for s in range(3):
        dirty, truths = inject_many(
            clean, n_per_type=10, seed=s,
            drop_range=(max(2, round(2 * k)), max(3, round(25 * k))),
            gap_range=(max(2, round(2 * k)), max(3, round(12 * k))),
            jump_range=(1.0, 4.0))
        m = evaluate_merge(clean, dirty, truths, c, (0.5,), frame_size=size)[0.5]
        tot["truth"] += m["truth"]
        tot["tp"] += m["tp"]
        big[1] += sum(g >= 13 for g in m["all_gaps"])
        big[0] += sum(g >= 13 for g in m["found_gaps"])
    print(f"{seq:18}{fps:>5.0f}{len(clean):>7}{tot['truth']:>7}{tot['tp']:>5}"
          f"{tot['tp'] / max(tot['truth'], 1):>8.2f}   {big[0]}/{big[1]}")
