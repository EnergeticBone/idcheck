import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import sys
import numpy as np

d = np.loadtxt(sys.argv[1], delimiter=",")
d = d[(d[:, 6] != 0) & (d[:, 7] == 1)]   # bỏ dòng conf=0, chỉ giữ class 1 (pedestrian)

ids = np.unique(d[:, 1]).astype(int)
print("số frame:", int(d[:, 0].max()), "| số track:", len(ids), "| số box:", len(d))

gaps = 0
for i in ids:
    fs = np.sort(d[d[:, 1] == i, 0]).astype(int)
    gaps += int((np.diff(fs) > 1).sum())
print("số chỗ đứt (gap) có sẵn trong GT:", gaps)