import sys, glob, os, yaml
from math import exp, hypot
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.patches as pa
from adapters.mot import load_mot_gt, load_seqinfo, load_fps
from core.config import scale_cfg
from core.rules import center, size
from core.merge import predict, _border_sides, _p

seq, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
cfg_file = sys.argv[4] if len(sys.argv) > 4 else "config_mot17.yaml"

gt = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)[0]
img_dir = os.path.join(os.path.dirname(os.path.dirname(gt)), "img1")
fsz = load_seqinfo(gt)
fps = load_fps(gt)
tracks = load_mot_gt(gt)
A, B = tracks[a], tracks[b]

cfg = scale_cfg(yaml.safe_load(open(cfg_file)), fps)
p = _p(cfg, A.label)

gap = B.start - A.end
ea = A.boxes[A.end]
sb = B.boxes[B.start]
ratio = size(sb) / size(ea)
px, py = predict(A, gap)
bx, by = center(sb)
dist = hypot(px - bx, py - by) / size(ea)

mg = p.get("border_margin", 15)
touch = _border_sides(ea, fsz, mg) & _border_sides(sb, fsz, mg)
old_score = round(exp(-dist), 3)
new_cost = dist + (p.get("border_penalty", 2.0) if touch else 0)
new_score = round(exp(-new_cost), 3)

# Calculate predicted box coordinates on B.start frame
wa, ha = ea.x2 - ea.x1, ea.y2 - ea.y1
pred_box = (px - wa / 2, py - ha / 2, wa, ha)

fig, axes = plt.subplots(1, 2, figsize=(16, 6))

# Left plot: Track A ending frame
axes[0].imshow(mpimg.imread(f"{img_dir}/{A.end:06d}.jpg"))
axes[0].add_patch(pa.Rectangle((ea.x1, ea.y1), ea.x2 - ea.x1, ea.y2 - ea.y1,
                               fill=False, edgecolor="lime", linewidth=2.5, label=f"Track {A.id}"))
axes[0].set_title(f"Track {A.id} (kết thúc tại frame {A.end})", fontsize=13, fontweight="bold")
axes[0].axis("off")

# Right plot: Track B starting frame + Predicted box of A
axes[1].imshow(mpimg.imread(f"{img_dir}/{B.start:06d}.jpg"))
axes[1].add_patch(pa.Rectangle((sb.x1, sb.y1), sb.x2 - sb.x1, sb.y2 - sb.y1,
                               fill=False, edgecolor="red", linewidth=2.5, label=f"Track {B.id} thực tế"))
axes[1].add_patch(pa.Rectangle((pred_box[0], pred_box[1]), pred_box[2], pred_box[3],
                               fill=False, edgecolor="cyan", linestyle="--", linewidth=2, label="Vị trí A dự đoán"))
axes[1].set_title(f"Track {B.id} (bắt đầu tại frame {B.start})", fontsize=13, fontweight="bold")
axes[1].legend(loc="upper right", fontsize=10)
axes[1].axis("off")

border_info = f"Chạm mép: {sorted(touch)} (phạt +{p.get('border_penalty', 2.0)})" if touch else "Không chạm mép"
title_text = (
    f"{seq} | Ghép Track {a} -> {b} | Gap: {gap} frame ({gap/fps:.2f}s) | Tỉ lệ cỡ: x{ratio:.2f}\n"
    f"Khoảng cách lệch: {dist:.2f} | {border_info} | Điểm CŨ: {old_score:.3f} ➔ Điểm MỚI: {new_score:.3f}"
)
fig.suptitle(title_text, fontsize=14, y=0.98, fontweight="bold")

os.makedirs("data/review", exist_ok=True)
out = f"data/review/{seq}_{a}_{b}.png"
fig.savefig(out, dpi=110, bbox_inches="tight")
plt.close(fig)
print(f"đã lưu {out} | Score Cũ={old_score:.3f} -> Mới={new_score:.3f}")