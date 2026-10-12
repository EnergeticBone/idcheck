import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os, sys, csv, random
from math import hypot
from adapters.cvat_api import load_task_tracks
from core.rules import center

task_id, seed = int(sys.argv[1]), int(sys.argv[2])
n = int(sys.argv[3]) if len(sys.argv) > 3 else 8
tracks, _ = load_task_tracks(os.environ["CVAT_HOST"],
                             (os.environ["CVAT_USER"], os.environ["CVAT_PASS"]), task_id)
rng = random.Random(seed)
pool = [t for t, tr in tracks.items() if len(tr.boxes) >= 40]


def peer(tid, f):
    ref, best, bd = tracks[tid].boxes.get(f), None, 1e18
    if ref is None:
        return None
    for o, tr in tracks.items():
        b = tr.boxes.get(f)
        if o == tid or b is None or tr.label != tracks[tid].label:
            continue
        d = hypot(center(ref)[0] - center(b)[0], center(ref)[1] - center(b)[1])
        if d < bd:
            best, bd = o, d
    return best


rows, used = [], set()
for kind in ["fragment", "gap", "jump", "switch", "class_flip", "duplicate"]:
    k = 0
    for tid in rng.sample(pool, len(pool)):
        if k >= n:
            break
        if tid in used:
            continue
        fs = sorted(tracks[tid].boxes)
        f = rng.randint(fs[0] + 10, fs[-1] - 15)
        other = peer(tid, f) if kind == "switch" else ""
        if kind == "switch" and (other is None or other in used):
            continue
        mag = {"gap": rng.randint(2, 12), "fragment": rng.randint(2, 25),
               "jump": round(rng.uniform(1.0, 4.0), 1), "class_flip": rng.randint(3, 8)}.get(kind, "")
        rows.append(dict(type=kind, track_a=tid, track_b=other, frame=f, magnitude=mag,
                         done="", actual_track_b="", note=""))
        used.update({tid, other} - {""})
        k += 1

with open("data/cvat_export/error_plan.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print("kế hoạch:", len(rows), "lỗi -> data/cvat_export/error_plan.csv")
