import sys, glob, os
import numpy as np
from PIL import Image
from statistics import median
from adapters.mot import load_mot_gt
from core.rules import center

seq, frame = sys.argv[1], int(sys.argv[2])
gt = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)[0]
img_dir = os.path.join(os.path.dirname(os.path.dirname(gt)), "img1")
tracks = load_mot_gt(gt)
S = 2   # thu nhỏ ảnh 2 lần cho nhanh


def load(f):
    im = Image.open(f"{img_dir}/{f:06d}.jpg").convert("L")
    return np.asarray(im.resize((im.width // S, im.height // S)), dtype=np.float32)


def img_shift(a, b):
    """Độ dịch (dy, dx) của nội dung ảnh từ a sang b, theo px ảnh gốc."""
    a, b = a - a.mean(), b - b.mean()
    w = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    R = np.fft.fft2(b * w) * np.conj(np.fft.fft2(a * w))
    R /= np.abs(R) + 1e-9
    r = np.fft.ifft2(R).real
    iy, ix = np.unravel_index(np.argmax(r), r.shape)
    if iy > a.shape[0] // 2: iy -= a.shape[0]
    if ix > a.shape[1] // 2: ix -= a.shape[1]
    return iy * S, ix * S, float(r.max())


print("frame | ảnh dịch (dy, dx) | độ tin | box dịch trung vị (dy, dx) | số track")
prev = load(frame - 5)
for f in range(frame - 4, frame + 5):
    cur = load(f)
    dy, dx, conf = img_shift(prev, cur)
    prev = cur
    d = []
    for tr in tracks.values():
        if f in tr.boxes and f - 1 in tr.boxes:
            (x0, y0), (x1, y1) = center(tr.boxes[f - 1]), center(tr.boxes[f])
            d.append((y1 - y0, x1 - x0))
    if d:
        by, bx = np.median(np.array(d), axis=0)
        box = f"({by:6.1f}, {bx:6.1f})"
    else:
        box = "   (không có)"
    mark = "  <--" if f == frame else ""
    print(f"{f:<6}| ({dy:5d}, {dx:5d})     | {conf:.2f}   | {box:<27}| {len(d)}{mark}")
