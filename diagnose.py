import sys, glob, yaml
import numpy as np
from adapters.mot import load_mot_gt, load_fps
from core.config import scale_cfg
from core.inject import inject_many
from core.rules import run_rules, iou
from core.merge import suggest_merges

seq, n = sys.argv[1], int(sys.argv[2])
gt = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)[0]
cfg = yaml.safe_load(open(sys.argv[3] if len(sys.argv) > 3 else "config.yaml"))
fps = load_fps(gt)
cfg = scale_cfg(cfg, fps)
clean = load_mot_gt(gt)
dirty, truths = inject_many(clean, n_per_type=n, seed=42)

print("== duplicate trên dữ liệu SẠCH ==")
for e in run_rules(clean, cfg):
    if e["type"] != "duplicate":
        continue
    A, B = clean[e["track_id"]], clean[e["other_id"]]
    common = sorted(set(A.boxes) & set(B.boxes))
    ious = [iou(A.boxes[f], B.boxes[f]) for f in common]
    print(f"track {A.id} vs {B.id}: chung {len(common)} frame, IoU TB {np.mean(ious):.2f}, "
          f"frame đầu {e['frame']}, độ dài {len(A.boxes)}/{len(B.boxes)}")

print("\n== gap đã chèn mà KHÔNG bị phát hiện ==")
det = {(e["track_id"], e["frame"]) for e in run_rules(dirty, cfg) if e["type"] == "gap"}
for t in truths:
    if t["type"] == "gap" and (t["a"], t["frame"]) not in det:
        tr = dirty[t["a"]]
        fs = sorted(tr.boxes)
        nxt = next(f for f in fs if f > t["frame"])
        print(f"track {t['a']} sau frame {t['frame']}: thiếu {nxt - t['frame'] - 1} frame | "
              f"occluded trước={tr.boxes[t['frame']].occluded}, sau={tr.boxes[nxt].occluded}")

print("\n== merge gợi ý trên dữ liệu SẠCH ==")
for m in suggest_merges(clean, cfg):
    A, B = clean[m["a"]], clean[m["b"]]
    print(f"{m['a']} -> {m['b']} score {m['score']} | A kết thúc f{A.end}, "
          f"B bắt đầu f{B.start}, cách {B.start - A.end} frame")