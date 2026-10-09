import glob, yaml
from adapters.mot import load_mot_gt, load_fps
from core.config import scale_cfg
from core.inject import inject_many
from core.rules import run_rules, iou
from core.evaluate import _match

cfg0 = yaml.safe_load(open("config_mot17.yaml"))
for seq in yaml.safe_load(open("splits.yaml"))["tune"]:
    gt = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)[0]
    clean, fps = load_mot_gt(gt), load_fps(gt)
    k = fps / 30.0
    cfg = scale_cfg(cfg0, fps)
    for s in range(3):
        dirty, truths = inject_many(
            clean, n_per_type=10, seed=s,
            drop_range=(max(2, round(2 * k)), max(3, round(25 * k))),
            gap_range=(max(2, round(2 * k)), max(3, round(12 * k))),
            jump_range=(1.0, 4.0))
        for e in run_rules(dirty, cfg):
            if e["type"] != "duplicate" or any(_match(e, t, 3) for t in truths):
                continue
            A, B = dirty[e["track_id"]], dirty[e["other_id"]]
            common = sorted(set(A.boxes) & set(B.boxes))
            m = sum(iou(A.boxes[f], B.boxes[f]) for f in common) / len(common)
            near = sorted({t["type"] for t in truths if {t["a"], t["b"]} & {A.id, B.id}})
            print(f"{seq} seed {s}: {A.id} vs {B.id}, chung {len(common)} frame, "
                  f"IoU TB {m:.2f}, lỗi chèn liên quan: {near or 'không'}")
