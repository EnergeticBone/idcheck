import sys, glob, yaml
from collections import Counter
from adapters.mot import load_mot_gt, load_seqinfo, load_fps
from core.config import scale_cfg
from core.inject import inject_many
from core.rules import run_rules
from core.evaluate import evaluate_rules, evaluate_merge, TYPES

split = sys.argv[1]
cfg = yaml.safe_load(open(sys.argv[2] if len(sys.argv) > 2 else "config.yaml"))
n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
n_seeds = int(sys.argv[4]) if len(sys.argv) > 4 else 3
seqs = yaml.safe_load(open("splits.yaml"))[split]

THR = (0.0, 0.3, 0.5, 0.7)
BUCKETS = [(2, 5), (6, 12), (13, 99)]
rules = {k: Counter() for k in TYPES}
merge = {t: Counter() for t in THR}
gaps_all, gaps_found = [], {t: [] for t in THR}
clean_by_type, clean_alerts, frames = Counter(), 0, 0


def ratio(a, b):
    return a / b if b else float("nan")


for seq in seqs:
    found = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)
    if not found:
        print("BỎ QUA (thiếu dữ liệu):", seq)
        continue
    clean = load_mot_gt(found[0])
    size = load_seqinfo(found[0])
    fps = load_fps(found[0])
    cfg_s = scale_cfg(cfg, fps)
    k = fps / 30.0
    frames += max(max(tr.boxes) for tr in clean.values())
    ca = run_rules(clean, cfg_s)
    clean_alerts += len(ca)
    clean_by_type.update(e["type"] for e in ca)
    for s in range(n_seeds):
        dirty, truths = inject_many(
            clean, n_per_type=n, seed=s,
            drop_range=(max(2, round(2 * k)), max(3, round(25 * k))),
            gap_range=(max(2, round(2 * k)), max(3, round(12 * k))),
            jump_range=(1.0, 4.0))
        for kk, v in evaluate_rules(clean, dirty, truths, cfg_s).items():
            rules[kk].update(v)
        m = evaluate_merge(clean, dirty, truths, cfg_s, THR, frame_size=size)
        gaps_all += m[THR[0]]["all_gaps"]
        for t in THR:
            for key in ("truth", "sugg", "tp", "base", "fp"):
                merge[t][key] += m[t][key]
            gaps_found[t] += m[t]["found_gaps"]

print(f"\n== LUẬT | tập {split}: {', '.join(seqs)} ==")
print(f"cảnh báo trên dữ liệu SẠCH: {clean_alerts} / {frames} frame "
      f"= {100 * clean_alerts / max(frames, 1):.2f} mỗi 100 frame  {dict(clean_by_type)}")
print(f"{'loại':11}{'truth':>7}{'recall':>8}{'tp':>6}{'base':>6}{'side':>6}{'fp':>6}{'precision':>11}")
for k in TYPES:
    c = rules[k]
    print(f"{k:11}{c['truth']:>7}{ratio(c['found'], c['truth']):>8.2f}{c['tp']:>6}{c['base']:>6}"
          f"{c['side']:>6}{c['fp']:>6}{ratio(c['tp'], c['tp'] + c['fp']):>11.2f}")

print("\n== MERGE (fragment) ==")
print(f"{'score>=':8}{'truth':>7}{'sugg':>6}{'tp':>5}{'base':>6}{'fp':>5}{'prec':>7}{'strict':>8}{'recall':>8}")
for t in THR:
    c = merge[t]
    print(f"{t:<8}{c['truth']:>7}{c['sugg']:>6}{c['tp']:>5}{c['base']:>6}{c['fp']:>5}"
          f"{ratio(c['tp'], c['tp'] + c['fp']):>7.2f}{ratio(c['tp'], c['sugg']):>8.2f}"
          f"{ratio(c['tp'], c['truth']):>8.2f}")
print("\nrecall theo độ dài gap (số frame giữa hai đoạn):")
for t in THR:
    row = []
    for lo, hi in BUCKETS:
        tot = sum(lo <= g <= hi for g in gaps_all)
        got = sum(lo <= g <= hi for g in gaps_found[t])
        row.append(f"{lo}-{hi if hi < 99 else '+'}: {got}/{tot}")
    print(f"  score>={t}: " + " | ".join(row))
