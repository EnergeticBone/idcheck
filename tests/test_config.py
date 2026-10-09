from core.config import scale_cfg

BASE = {"default": {"min_missing_gap": 2, "max_missing_gap": 15, "max_merge_gap": 30,
                    "max_frame_step": 3, "max_disp_ratio": 1.0, "dup_min_frames": 10},
        "classes": {"pedestrian": {"max_disp_ratio": 0.6}}}


def test_same_fps_is_identity():
    assert scale_cfg(BASE, 30.0) == BASE


def test_low_fps_scaling():
    c = scale_cfg(BASE, 14.0)
    assert c["default"]["max_merge_gap"] == 14          # 30 * 14/30
    assert c["default"]["min_missing_gap"] == 2         # có sàn
    assert c["default"]["max_disp_ratio"] > 1.0         # mỗi frame dịch xa hơn
    assert c["classes"]["pedestrian"]["max_disp_ratio"] > 0.6
