import sys, glob, yaml
from collections import Counter
from adapters.mot import load_mot_gt, load_seqinfo, load_fps
from core.config import scale_cfg
from core.inject import inject_many
from core.evaluate import evaluate_merge

cfg = yaml.safe_load(open(sys.argv[1]))
seqs = yaml.safe_load(open("splits.yaml"))["tune"]
THR = (0.3, 0.5)
data = []
for seq in seqs:
    f = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)
    if not f:
        continue
    clean, size, fps = load_mot_gt(f[0]), load_seqinfo(f[0]), load_fps(f[0])
    k = fps / 30.0
    for s in range(3):
        dirty, truths = inject_many(
            clean, n_per_type=10, seed=s,
            drop_range=(max(2, round(2 * k)), max(3, round(25 * k))),
            gap_range=(max(2, round(2 * k)), max(3, round(12 * k))),
            jump_range=(1.0, 4.0))
        data.append((clean, dirty, truths, size, fps))

print("max_cost  score>= | truth  sugg   tp  base   fp | strict  recall")
for mc in (3.5, 2.7, 2.0, 1.6, 1.2, 0.8):
    tot = {t: Counter() for t in THR}
    for clean, dirty, truths, size, fps in data:
        c = scale_cfg(cfg, fps)
        c["default"]["merge_max_cost"] = mc
        m = evaluate_merge(clean, dirty, truths, c, THR, frame_size=size)
        for t in THR:
            for key in ("truth", "sugg", "tp", "base", "fp"):
                tot[t][key] += m[t][key]
    for t in THR:
        x = tot[t]
        print(f"{mc:<10}{t:<8}| {x['truth']:>5}{x['sugg']:>6}{x['tp']:>5}{x['base']:>6}{x['fp']:>5}"
              f" | {x['tp'] / max(x['sugg'], 1):>6.2f}{x['tp'] / max(x['truth'], 1):>8.2f}")
