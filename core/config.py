import copy

FRAME_KEYS = ("max_missing_gap", "max_merge_gap", "dup_min_frames")


def scale_cfg(cfg, fps, ref=30.0):
    """Co giãn các ngưỡng tính theo frame về đúng thời gian thực của chuỗi.
    cfg được viết cho `ref` fps."""
    k = fps / ref
    out = copy.deepcopy(cfg)

    def fix(d):
        for key in FRAME_KEYS:
            if key in d:
                d[key] = max(1, round(d[key] * k))
        if "min_missing_gap" in d:
            d["min_missing_gap"] = max(2, round(d["min_missing_gap"] * k))
        if "max_frame_step" in d:
            d["max_frame_step"] = max(2, round(d["max_frame_step"] * k))
        for key in ("max_disp_ratio", "swap_min_disp"):   # dịch chuyển mỗi frame
            if key in d:
                d[key] = d[key] / k

    fix(out.get("default", {}))
    for v in (out.get("classes") or {}).values():
        fix(v or {})
    return out