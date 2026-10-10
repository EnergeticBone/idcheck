import sys, glob, os, yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.patches as pa
from adapters.mot import load_mot_gt, load_fps
from core.config import scale_cfg
from core.rules import run_rules

cfg0 = yaml.safe_load(open(sys.argv[1]))
out_dir = "data/review/clean_alerts"
os.makedirs(out_dir, exist_ok=True)

for seq in yaml.safe_load(open("splits.yaml"))["report"]:
    gt = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)[0]
    img_dir = os.path.join(os.path.dirname(os.path.dirname(gt)), "img1")
    clean, fps = load_mot_gt(gt), load_fps(gt)
    for e in run_rules(clean, scale_cfg(cfg0, fps)):
        print(seq, e["type"], "track", e["track_id"], "other", e.get("other_id"),
              "frame", e["frame"], "|", e["detail"])
        A = clean[e["track_id"]]
        if e["type"] == "jump":
            prev = max(f for f in A.boxes if f < e["frame"])
            panels = [(prev, [(A.boxes[prev], "lime")]), (e["frame"], [(A.boxes[e["frame"]], "red")])]
        elif e["type"] == "duplicate":
            B = clean[e["other_id"]]
            panels = [(e["frame"], [(A.boxes[e["frame"]], "lime"), (B.boxes[e["frame"]], "red")])]
        else:
            panels = [(e["frame"], [(A.boxes[e["frame"]], "lime")])]
        fig, axes = plt.subplots(1, len(panels), figsize=(8 * len(panels), 5), squeeze=False)
        for ax, (f, boxes) in zip(axes[0], panels):
            ax.imshow(mpimg.imread(f"{img_dir}/{f:06d}.jpg"))
            for bx, color in boxes:
                ax.add_patch(pa.Rectangle((bx.x1, bx.y1), bx.x2 - bx.x1, bx.y2 - bx.y1,
                                          fill=False, edgecolor=color, linewidth=2))
            ax.set_title(f"{seq} | frame {f}")
            ax.axis("off")
        path = f"{out_dir}/{seq}_{e['type']}_{e['track_id']}_{e['frame']}.png"
        fig.savefig(path, dpi=90, bbox_inches="tight")
        plt.close(fig)
