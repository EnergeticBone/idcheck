from core.schema import Box, Track
from core.rules import check_gaps, check_jumps, check_class_flips, check_duplicates

CFG = {"default": dict(min_missing_gap=2, max_missing_gap=15, max_frame_step=3,
                       max_disp_ratio=1.0, max_scale=1.8, dup_iou=0.7, dup_min_frames=10)}


def mk(tid, frames, x0=0.0, label="pedestrian", **kw):
    return Track(tid, label, {f: Box(f, x0 + f, 0, x0 + f + 10, 20, label, **kw) for f in frames})


def test_gap_detected():
    tr = mk(1, list(range(0, 10)) + list(range(14, 30)))
    errs = check_gaps(tr, CFG)
    assert len(errs) == 1 and errs[0]["frame"] == 9


def test_gap_ignored_when_occluded():
    tr = mk(1, list(range(0, 10)) + list(range(14, 30)), occluded=True)
    assert check_gaps(tr, CFG) == []


def test_jump_reported_once():
    tr = mk(1, range(0, 30))
    tr.boxes[15].x1 += 40
    tr.boxes[15].x2 += 40
    errs = check_jumps(tr, CFG)
    assert len(errs) == 1 and errs[0]["frame"] == 15


def test_normal_motion_is_not_jump():
    assert check_jumps(mk(1, range(0, 30)), CFG) == []


def test_class_flip():
    tr = mk(1, range(0, 30))
    for f in range(10, 14):
        tr.boxes[f].label = "car"
    errs = check_class_flips(tr, CFG)
    assert len(errs) == 1 and errs[0]["frame"] == 10


def test_duplicate():
    # x0=1.0 -> IoU = 180/220 ≈ 0.82 > dup_iou
    tracks = {1: mk(1, range(0, 30)), 2: mk(2, range(0, 30), x0=1.0), 3: mk(3, range(0, 30), x0=500)}
    errs = check_duplicates(tracks, CFG)
    assert len(errs) == 1 and (errs[0]["track_id"], errs[0]["other_id"]) == (1, 2)


def test_near_but_not_duplicate():
    # x0=2.0 -> IoU ≈ 0.667 < dup_iou, hai người đứng sát nhau chứ không trùng
    tracks = {1: mk(1, range(0, 30)), 2: mk(2, range(0, 30), x0=2.0)}
    assert check_duplicates(tracks, CFG) == []