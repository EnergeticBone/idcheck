import copy
from core.schema import Box, Track
from core.inject import jump_box, swap_ids
from core.evaluate import evaluate_rules

CFG = {"default": dict(min_missing_gap=2, max_missing_gap=15, max_frame_step=3,
                       max_disp_ratio=1.0, max_scale=1.8, dup_iou=0.7,
                       dup_min_frames=10, dup_min_ratio=0.6,
                       swap_min_disp=0.5, swap_cross_ratio=0.5)}


def mk(tid, x0=0.0, n=60):
    return Track(tid, "pedestrian", {f: Box(f, x0 + f, 0, x0 + f + 10, 20, "pedestrian") for f in range(n)})


def test_jump_is_true_positive():
    clean = {1: mk(1), 2: mk(2, x0=300)}
    dirty = copy.deepcopy(clean)
    truth = jump_box(dirty, 1, 30, ratio=3.0)
    r = evaluate_rules(clean, dirty, [truth], CFG)["jump"]
    assert r["truth"] == 1 and r["found"] == 1 and r["fp"] == 0


def test_switch_counts_jumps_as_side_effects():
    clean = {1: mk(1), 2: mk(2, x0=30)}
    dirty = copy.deepcopy(clean)
    truth = swap_ids(dirty, 1, 2, t=20)
    res = evaluate_rules(clean, dirty, [truth], CFG)
    assert res["switch"]["found"] == 1
    assert res["jump"]["side"] >= 1 and res["jump"]["fp"] == 0
