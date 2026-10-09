from core.schema import Box, Track
from core.rules import check_gaps, check_jumps, check_class_flips, check_duplicates, check_switches
from core.inject import swap_ids

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

def test_crossing_tracks_are_not_duplicates():
    # hai người đi ngược chiều, chỉ chồng nhau một đoạn ngắn
    a = mk(1, range(0, 100), x0=0.0)
    b = Track(2, "pedestrian", {f: Box(f, 100 - f, 0, 110 - f, 20, "pedestrian") for f in range(0, 100)})
    cfg = {"default": dict(CFG["default"], dup_min_ratio=0.6)}
    assert check_duplicates({1: a, 2: b}, cfg) == []

def test_brief_overlap_is_not_duplicate():
    a = mk(1, range(0, 100))
    b = Track(2, "pedestrian", {})
    for f in range(100):
        x = f + 1.0 if 40 <= f < 52 else f + 300.0   # chỉ bám sát 12 frame
        b.boxes[f] = Box(f, x, 0, x + 10, 20, "pedestrian")
    assert check_duplicates({1: a, 2: b}, CFG) == []


def test_true_duplicate_still_detected():
    tracks = {1: mk(1, range(0, 60)), 2: mk(2, range(0, 60), x0=1.0)}
    errs = check_duplicates(tracks, CFG)
    assert len(errs) == 1 and errs[0]["other_id"] == 2


def test_gap_with_occlusion_check_off():
    cfg = {"default": dict(CFG["default"], skip_occluded_gap=False)}
    tr = mk(1, list(range(0, 10)) + list(range(14, 30)), occluded=True)
    assert len(check_gaps(tr, cfg)) == 1


def test_gap_with_occlusion_check_on():
    cfg = {"default": dict(CFG["default"], skip_occluded_gap=True)}
    tr = mk(1, list(range(0, 10)) + list(range(14, 30)), occluded=True)
    assert check_gaps(tr, cfg) == []

def test_close_overlapping_swap_not_flagged():
    # hai track cách nhau 3 px (< 0.3 cỡ box): hoán ID không phân biệt được
    tr = {1: mk(1, range(0, 40), x0=0.0), 2: mk(2, range(0, 40), x0=3.0)}
    swap_ids(tr, 1, 2, t=20)
    assert check_switches(tr, CFG) == []


def test_slow_but_separated_swap_detected():
    tr = {1: mk(1, range(0, 40), x0=0.0), 2: mk(2, range(0, 40), x0=15.0)}
    swap_ids(tr, 1, 2, t=20)
    assert len(check_switches(tr, CFG)) == 1