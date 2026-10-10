import sys, glob
import numpy as np
from adapters.mot import load_mot_gt
from core.rules import center

seq, frame = sys.argv[1], int(sys.argv[2])
gt = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)[0]
tracks = load_mot_gt(gt)
print("frame  số track  median dx  median dy  MAD dx  MAD dy")
for f in range(frame - 4, frame + 5):
    d = []
    for tr in tracks.values():
        if f in tr.boxes and f - 1 in tr.boxes:
            (x0, y0), (x1, y1) = center(tr.boxes[f - 1]), center(tr.boxes[f])
            d.append((x1 - x0, y1 - y0))
    if len(d) < 2:
        print(f"{f:<7}{len(d):<10}(quá ít track)")
        continue
    d = np.array(d)
    med = np.median(d, axis=0)
    mad = np.median(np.abs(d - med), axis=0)
    mark = "  <--" if f == frame else ""
    print(f"{f:<7}{len(d):<10}{med[0]:>9.1f}{med[1]:>11.1f}{mad[0]:>8.1f}{mad[1]:>8.1f}{mark}")
