import copy

FRAME_KEYS = ("max_missing_gap", "max_merge_gap", "dup_min_frames")

def resolve_cfg(cfg, fps):
    """Đổi khóa theo giây sang khóa theo frame mà rules.py và merge.py đang dùng."""
    conv = {
        "min_missing_gap_s": ("min_missing_gap", lambda v: max(1, round(v * fps))),
        "max_missing_gap_s": ("max_missing_gap", lambda v: max(1, round(v * fps))),
        "max_frame_step_s":  ("max_frame_step",  lambda v: max(1, round(v * fps))),
        "max_merge_gap_s":   ("max_merge_gap",   lambda v: max(1, round(v * fps))),
        "dup_min_s":         ("dup_min_frames",  lambda v: max(2, round(v * fps))),
        "max_speed":         ("max_disp_ratio",  lambda v: v / fps),
        "merge_gap_cost_s":  ("merge_gap_cost",  lambda v: v / fps),
    }
    out = copy.deepcopy(cfg)

    def apply(d):
        for src, (dst, f) in conv.items():
            if src in d:
                d[dst] = f(d.pop(src))

    apply(out.setdefault("default", {}))
    for d in (out.get("classes") or {}).values():
        if d:
            apply(d)
    out["fps"] = float(fps)
    return out

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