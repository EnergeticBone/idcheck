import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import sys
from collections import Counter


def frozen_runs(tr, tol=0.5, min_len=5):
    fs = sorted(f for f, b in tr.boxes.items() if not b.outside)
    runs, i = [], 0
    while i < len(fs):
        j = i
        while (j + 1 < len(fs) and fs[j + 1] == fs[j] + 1
               and all(abs(getattr(tr.boxes[fs[j + 1]], a) - getattr(tr.boxes[fs[i]], a)) <= tol
                       for a in ("x1", "y1", "x2", "y2"))):
            j += 1
        n = j - i + 1
        if n >= min_len:
            inner_kf = any(tr.boxes[f].keyframe for f in fs[i + 1:j + 1])
            runs.append((fs[i], fs[j], n, j == len(fs) - 1, inner_kf))
        i = j + 1
    return runs


def report(tracks, tol=0.5, min_len=5):
    total_boxes = sum(len(t.boxes) for t in tracks.values())
    allruns = [(tid, r) for tid, t in tracks.items() for r in frozen_runs(t, tol, min_len)]
    buckets = Counter()
    for _, (a, b, n, tail, kf) in allruns:
        buckets["5-7" if n < 8 else "8-15" if n < 16 else "16-40" if n < 41 else "41+"] += 1
    print(f"track: {len(tracks)} | box: {total_boxes} | đoạn đứng yên >= {min_len} frame: {len(allruns)}")
    print("  theo độ dài:", dict(buckets))
    print("  nằm ở CUỐI track:", sum(1 for _, r in allruns if r[3]),
          "| có keyframe nằm giữa đoạn:", sum(1 for _, r in allruns if r[4]))
    for tid, (a, b, n, tail, kf) in sorted(allruns, key=lambda x: -x[1][2])[:8]:
        print(f"  track {tid}: frame {a}-{b} ({n} frame){' [cuối track]' if tail else ''}"
              f"{' [có keyframe]' if kf else ''}")
