import json, sys, pathlib
from adapters.mot import load_mot_gt
from core.inject import inject_many

seq = sys.argv[1]                     # ví dụ MOT17-09-FRCNN
n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
gt = f"data/clean/MOT17/train/{seq}/gt/gt.txt"

clean = load_mot_gt(gt)
dirty, truths = inject_many(clean, n_per_type=n, seed=42)

out = pathlib.Path("data/injected"); out.mkdir(parents=True, exist_ok=True)
(out / f"{seq}.truth.json").write_text(json.dumps(truths, indent=1))
print(seq, "| track sạch:", len(clean), "| track sau chèn:", len(dirty))
from collections import Counter
print(dict(Counter(t["type"] for t in truths)))