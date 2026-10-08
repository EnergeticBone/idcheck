import numpy as np
from core.schema import Box, Track

# MOT17: class 1 = pedestrian. Các class khác (2 người trên xe, 7 người đứng yên,
# 8 distractor, 12 phản chiếu) tạm bỏ qua cho bản đầu.
CLASS_NAMES = {1: "pedestrian"}


def load_mot_gt(path, classes=(1,), occluded_below=0.5):
    """Đọc gt.txt của MOT17 -> dict[int, Track].

    Mỗi dòng: frame, id, x, y, w, h, conf, class, visibility.
    conf = 0 nghĩa là bỏ qua dòng đó.
    Cờ occluded được suy ra từ visibility (< occluded_below).
    """
    data = np.loadtxt(path, delimiter=",", ndmin=2)
    tracks = {}
    for row in data:
        frame, tid, x, y, w, h, conf, cls, vis = row[:9]
        if conf == 0 or int(cls) not in classes:
            continue
        tid, frame = int(tid), int(frame)
        label = CLASS_NAMES.get(int(cls), str(int(cls)))
        tr = tracks.setdefault(tid, Track(tid, label))
        tr.boxes[frame] = Box(
            frame, float(x), float(y), float(x + w), float(y + h), label,
            occluded=bool(vis < occluded_below),
        )
    return tracks