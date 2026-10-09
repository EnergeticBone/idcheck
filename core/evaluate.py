from core.rules import run_rules
from core.merge import suggest_merges

TYPES = ["gap", "jump", "class_flip", "duplicate", "switch"]


def _ids(e):
    return {x for x in (e.get("track_id"), e.get("other_id")) if x is not None}


def _tids(t):
    return {x for x in (t["a"], t["b"]) if x is not None}


def _key(e):
    return (e["type"], e["track_id"], e.get("other_id"), e["frame"])


def _match(e, t, tol):
    if e["type"] != t["type"]:
        return False
    k = t["type"]
    if k in ("gap", "jump", "class_flip"):
        return e["track_id"] == t["a"] and abs(e["frame"] - t["frame"]) <= tol
    if k == "duplicate":
        return _ids(e) == _tids(t)
    if k == "switch":
        return _ids(e) == _tids(t) and abs(e["frame"] - t["frame"]) <= tol
    return False


def evaluate_rules(clean, dirty, truths, cfg, tol=3, near=5):
    """Mỗi cảnh báo trên dữ liệu có lỗi thuộc một nhóm:
    tp = khớp lỗi đã chèn cùng loại; base = đã có sẵn trên dữ liệu sạch;
    side = tác dụng phụ của lỗi chèn loại khác; fp = báo sai."""
    base = {_key(e) for e in run_rules(clean, cfg)}
    res = {k: dict(truth=0, found=0, tp=0, base=0, side=0, fp=0) for k in TYPES}
    for t in truths:
        if t["type"] in res:
            res[t["type"]]["truth"] += 1
    found = set()
    for e in run_rules(dirty, cfg):
        k = e["type"]
        hit = [i for i, t in enumerate(truths) if _match(e, t, tol)]
        if hit:
            res[k]["tp"] += 1
            found.update(hit)
        elif _key(e) in base:
            res[k]["base"] += 1
        elif any(t["type"] != k and (_ids(e) & _tids(t)) and abs(e["frame"] - t["frame"]) <= near
                 for t in truths):
            res[k]["side"] += 1
        else:
            res[k]["fp"] += 1
    for i in found:
        res[truths[i]["type"]]["found"] += 1
    return res


def evaluate_merge(clean, dirty, truths, cfg, thresholds=(0.0, 0.3, 0.5, 0.7), frame_size=None):
    T = {(t["a"], t["b"]) for t in truths if t["type"] == "fragment"}
    gap_of = {k: dirty[k[1]].start - dirty[k[0]].end for k in T}
    base = {(m["a"], m["b"]) for m in suggest_merges(clean, cfg, frame_size=frame_size)}
    all_sugg = suggest_merges(dirty, cfg, min_score=0.0, frame_size=frame_size)
    out = {}
    for thr in thresholds:
        pairs = {(m["a"], m["b"]) for m in all_sugg if m["score"] >= thr}
        tp = pairs & T
        out[thr] = dict(truth=len(T), sugg=len(pairs), tp=len(tp),
                        base=len((pairs - T) & base), fp=len(pairs - T - base),
                        found_gaps=[gap_of[k] for k in tp], all_gaps=list(gap_of.values()))
    return out
