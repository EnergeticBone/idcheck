import numpy as np
import pytest
from core.schema import Box, Track
from core.reid import (
    ReIDExtractor,
    get_reid_extractor,
    compare_crops,
    verify_switch_candidate,
    compute_merge_visual_similarity,
    reid_filter_and_enhance_issues,
    find_additional_visual_merges,
)


def test_reid_extractor_initialization():
    extractor = get_reid_extractor()
    assert extractor is not None
    # Model should be loaded or using fallback
    assert hasattr(extractor, "use_onnx")


def test_reid_crop_box():
    extractor = get_reid_extractor()
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[20:60, 30:70] = 200

    # With dict
    box_dict = {"x": 30, "y": 20, "w": 40, "h": 40}
    crop = extractor.crop_box(img, box_dict, margin_ratio=0.0)
    assert crop is not None
    assert crop.shape[0] == 40
    assert crop.shape[1] == 40

    # With Box schema object
    box_obj = Box(frame=1, x1=30, y1=20, x2=70, y2=60, label="Car")
    crop_obj = extractor.crop_box(img, box_obj, margin_ratio=0.0)
    assert crop_obj is not None
    assert crop_obj.shape[0] == 40
    assert crop_obj.shape[1] == 40

    # Out of bounds / invalid box
    invalid_box = {"x": 0, "y": 0, "w": 0, "h": 0}
    assert extractor.crop_box(img, invalid_box) is None

    # Micro box: 15x15 px (below 20x20 px threshold)
    micro_box = {"x": 20, "y": 20, "w": 15, "h": 15}
    assert extractor.crop_box(img, micro_box) is None, "Micro-box below min threshold should be rejected by Re-ID gating"

    # Normal box: 25x25 px
    normal_box = {"x": 20, "y": 20, "w": 25, "h": 25}
    assert extractor.crop_box(img, normal_box) is not None


def test_reid_embeddings_and_similarity():
    extractor = get_reid_extractor()

    # Red crop vs Red crop vs Blue crop
    red_crop = np.zeros((64, 48, 3), dtype=np.uint8)
    red_crop[:, :] = [0, 0, 240]  # BGR Red

    red_crop_2 = np.zeros((64, 48, 3), dtype=np.uint8)
    red_crop_2[:, :] = [0, 10, 230]

    blue_crop = np.zeros((64, 48, 3), dtype=np.uint8)
    blue_crop[:, :] = [240, 0, 0]  # BGR Blue

    sim_same = compare_crops(red_crop, red_crop)
    sim_similar = compare_crops(red_crop, red_crop_2)
    sim_diff = compare_crops(red_crop, blue_crop)

    assert sim_same >= 0.98
    assert sim_similar > 0.85
    assert sim_diff < sim_similar


def test_verify_switch_candidate():
    img_a = np.zeros((100, 100, 3), dtype=np.uint8)
    img_a[:, :] = [0, 0, 255]  # Red

    img_b = np.zeros((100, 100, 3), dtype=np.uint8)
    img_b[:, :] = [255, 0, 0]  # Blue

    # Frame 0: A is at left (Red), B is at right (Blue)
    f0 = np.zeros((100, 200, 3), dtype=np.uint8)
    f0[:, 0:100] = [0, 0, 255]
    f0[:, 100:200] = [255, 0, 0]

    # Frame 1: True Switch -> Track A at left becomes Blue, Track B at right becomes Red
    f1_switch = np.zeros((100, 200, 3), dtype=np.uint8)
    f1_switch[:, 0:100] = [255, 0, 0]
    f1_switch[:, 100:200] = [0, 0, 255]

    box_left = {"x": 10, "y": 10, "w": 40, "h": 40}
    box_right = {"x": 110, "y": 10, "w": 40, "h": 40}

    # Case A: True switch
    def getter_switch(fid):
        return f0 if fid == 0 else f1_switch

    confirmed, swap_score, keep_score = verify_switch_candidate(
        getter_switch, box_left, box_left, box_right, box_right, 0, 1, margin_threshold=0.05
    )
    assert confirmed is True
    assert swap_score > keep_score

    # Case B: No switch (both objects kept their original colors)
    def getter_normal(fid):
        return f0

    confirmed_norm, swap_score_norm, keep_score_norm = verify_switch_candidate(
        getter_normal, box_left, box_left, box_right, box_right, 0, 1, margin_threshold=0.05
    )
    assert confirmed_norm is False
    assert keep_score_norm > swap_score_norm


def test_reid_filter_and_enhance_issues():
    # Setup dummy tracks
    t1 = Track(id=1, label="Car", boxes={
        0: Box(frame=0, x1=10, y1=10, x2=50, y2=50, label="Car"),
        1: Box(frame=1, x1=10, y1=10, x2=50, y2=50, label="Car"),
    })
    t2 = Track(id=2, label="Car", boxes={
        0: Box(frame=0, x1=110, y1=10, x2=150, y2=50, label="Car"),
        1: Box(frame=1, x1=110, y1=10, x2=150, y2=50, label="Car"),
    })
    tracks = {1: t1, 2: t2}

    # Frame image: objects don't switch (constant red on left, blue on right)
    f_img = np.zeros((100, 200, 3), dtype=np.uint8)
    f_img[:, 0:100] = [0, 0, 255]
    f_img[:, 100:200] = [255, 0, 0]

    issues = [
        {
            "id": "ID Switch Candidate:1:1:1_2",
            "type": "ID Switch Candidate",
            "frame": 1,
            "track_id": 1,
            "message": "nghi hoán ID giữa track 1 và 2",
            "fix_data": {"a": 1, "b": 2, "from_frame": 1, "pair": "1_2"}
        }
    ]

    # Re-ID should prune this false switch because appearance did NOT swap
    cleaned = reid_filter_and_enhance_issues(issues, tracks, lambda fid: f_img)
    assert len(cleaned) == 0, "False switch candidate should be pruned by Re-ID"


def test_find_additional_visual_merges():
    # Track 1 ends at frame 5 (Red vehicle)
    # Track 2 starts at frame 8 (Red vehicle nearby)
    t1 = Track(id=1, label="Car", boxes={
        f: Box(frame=f, x1=20 + f*2, y1=30, x2=60 + f*2, y2=70, label="Car")
        for f in range(1, 6)
    })
    t2 = Track(id=2, label="Car", boxes={
        f: Box(frame=f, x1=20 + f*2, y1=30, x2=60 + f*2, y2=70, label="Car")
        for f in range(8, 14)
    })
    tracks = {1: t1, 2: t2}

    # Video frames with red car
    def getter(fid):
        img = np.zeros((150, 150, 3), dtype=np.uint8)
        img[30:70, 20:60] = [0, 0, 230]
        return img

    merges = find_additional_visual_merges(tracks, getter, cfg={}, max_gap=10, min_visual_sim=0.70)
    assert len(merges) >= 1
    assert merges[0]["track_id"] == 1
    assert merges[0]["fix_data"]["target"] == 2
    assert merges[0]["fix_data"]["reid_match"] is True
