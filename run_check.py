import sys, glob, yaml
from collections import Counter
from adapters.mot import load_mot_gt
from core.inject import inject_many
from core.rules import run_rules
from core.merge import suggest_merges

seq, n = sys.argv[1], int(sys.argv[2])
gt = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)[0]
cfg = yaml.safe_load(open("config.yaml"))

clean = load_mot_gt(gt)
dirty, truths = inject_many(clean, n_per_type=n, seed=42)

c = Counter(e["type"] for e in run_rules(clean, cfg))
d = Counter(e["type"] for e in run_rules(dirty, cfg))
t = Counter(x["type"] for x in truths)

fragments = [x for x in truths if x["type"] == "fragment"]
T = {(f["a"], f["b"]) for f in fragments}
print(seq)
print("  cảnh báo trên dữ liệu SẠCH (≈ false positive):", dict(c))
print("  lỗi đã chèn                                   :", dict(t))
print("  cảnh báo trên dữ liệu có lỗi                  :", dict(d))
print("  merge: gợi ý trên dữ liệu SẠCH (coi như đều sai):", len(suggest_merges(clean, cfg)))
for thr in (0.0, 0.5, 0.7):
    S = {(m["a"], m["b"]) for m in suggest_merges(dirty, cfg, min_score=thr)}
    tp = len(S & T)
    prec = tp / len(S) if S else float("nan")
    rec = tp / len(T) if T else float("nan")
    print(f"  merge score>={thr}: gợi ý={len(S)}, đúng={tp}, precision={prec:.2f}, recall={rec:.2f}")