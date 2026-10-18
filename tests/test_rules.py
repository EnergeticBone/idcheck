from core.schema import Box, Track
from core.rules import (
    check_invalid_boxes,
    check_gaps,
    check_jumps,
    check_class_flips,
    check_duplicates,
    check_switches,
    build_center_index,
)
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
    assert errs[0].get("subtype") == "spike"
    assert "Spike" in errs[0]["detail"]


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


def mk_group(dy=30.0, f_shift=20, n_tracks=6, n=40):
    tracks = {}
    for i in range(n_tracks):
        boxes = {}
        for f in range(n):
            y = i * 5 + (dy if f >= f_shift else 0.0)
            boxes[f] = Box(f, 10 * i + f, y, 10 * i + f + 10, y + 20, "pedestrian")
        tracks[i + 1] = Track(i + 1, "pedestrian", boxes)
    return tracks


CAM = {"default": dict(CFG["default"], compensate_camera=True, cam_min_tracks=4)}


def test_camera_shake_is_suppressed():
    tracks = mk_group(dy=30.0)
    assert sum(len(check_jumps(t, CFG)) for t in tracks.values()) == 6
    idx = build_center_index(tracks)
    assert sum(len(check_jumps(t, CAM, idx)) for t in tracks.values()) == 0


def test_single_jump_survives_compensation():
    tracks = mk_group(dy=0.0)
    b = tracks[1].boxes[20]
    b.x1 += 60
    b.x2 += 60
    idx = build_center_index(tracks)
    assert len(check_jumps(tracks[1], CAM, idx)) >= 1


def test_invalid_boxes_detected():
    # 1. Box có chiều rộng hoặc chiều cao <= 0
    tr1 = Track(1, "car", {
        0: Box(0, 100, 100, 90, 150, "car"),   # x2 < x1: width âm
        1: Box(1, 100, 100, 150, 100, "car"),   # y2 == y1: height = 0
    })
    errs1 = check_invalid_boxes(tr1, CFG)
    assert len(errs1) == 2
    assert all(e["type"] == "invalid_box" for e in errs1)

    # 2. Box nằm hoàn toàn ngoài khung hình
    tr2 = Track(2, "car", {
        0: Box(0, -100, 100, -20, 150, "car"),     # x2 <= 0 (ngoài mép trái)
        1: Box(1, 1300, 100, 1350, 150, "car"),    # x1 >= 1280 (ngoài mép phải)
    })
    errs2 = check_invalid_boxes(tr2, CFG, frame_size=(1280, 720))
    assert len(errs2) == 2
    assert all(e["type"] == "invalid_box" for e in errs2)

    # 3. Box hợp lệ hoặc có cờ outside không bị bắt
    tr3 = Track(3, "car", {
        0: Box(0, 100, 100, 150, 150, "car"),
        1: Box(1, 100, 100, 100, 100, "car", outside=True),  # outside marker
    })
    assert check_invalid_boxes(tr3, CFG, frame_size=(1280, 720)) == []


def test_is_horizon_box():
    from core.rules import is_horizon_box

    cfg = {
        "default": {
            "enable_horizon_filter": True,
            "horizon_band": [0.32, 0.54],
            "horizon_min_h": 20,
            "horizon_min_w": 18,
            "horizon_min_area": 360,
        }
    }
    frame_size = (1280, 720)

    # Box in horizon band (y_center = 300 / 720 = 0.417), small size: 15x15 -> True
    b_small_horizon = Box(1, 100, 290, 115, 305, "car")  # w=15, h=15
    assert is_horizon_box(b_small_horizon, cfg, frame_size) is True

    # Box in horizon band, normal size: 50x40 -> False
    b_large_horizon = Box(1, 100, 280, 150, 320, "car")  # w=50, h=40
    assert is_horizon_box(b_large_horizon, cfg, frame_size) is False

    # Box outside horizon band (foreground: y_center = 600 / 720 = 0.83), small size: 15x15 -> False (don't filter foreground micro-parts!)
    b_small_foreground = Box(1, 100, 590, 115, 605, "car")
    assert is_horizon_box(b_small_foreground, cfg, frame_size) is False


def test_gap_detected_with_interpolated_boxes():
    # Annotator missing frames 10..13 (4 frames), CVAT auto-interpolated them with keyframe=False
    boxes = {f: Box(f, f, 0, f + 10, 20, "car", keyframe=(f not in (10, 11, 12, 13))) for f in range(0, 30)}
    tr = Track(1, "car", boxes)
    errs = check_gaps(tr, CFG)
    assert len(errs) == 1
    assert errs[0]["frame"] == 9
    assert errs[0]["gap"] == 4
    assert "thiếu 4 frame" in errs[0]["detail"]


def test_gap_detected_bdd100k_5fps_config():
    import yaml
    from pathlib import Path
    from core.config import resolve_cfg

    cfg_file = Path(__file__).resolve().parent.parent / "config_project_3.yaml"
    with open(cfg_file, "r", encoding="utf-8") as f:
        raw_cfg = yaml.safe_load(f)
    cfg = resolve_cfg(raw_cfg, fps=5.0)

    # 5 FPS -> min_missing_gap = 1, max_missing_gap = 5
    # Missing 2 frames: 11, 12
    tr = mk(1, list(range(0, 11)) + list(range(13, 25)), label="car")
    errs = check_gaps(tr, cfg)
    assert len(errs) == 1
    assert errs[0]["frame"] == 10
    assert errs[0]["gap"] == 2