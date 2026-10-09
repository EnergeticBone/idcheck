from math import hypot
from core.schema import Box, Track
from core.rules import center, size
from core.merge import predict, suggest_merges
from core.inject import split_track, inject_many

CFG = {
    "default": {
        "max_merge_gap": 30,
        "max_merge_dist": 2.0,
    }
}


def mk_moving(tid, frames, x0=10.0, y0=20.0, vx=2.0, vy=1.0, w=10.0, h=20.0, label="pedestrian", **kw):
    boxes = {}
    for f in frames:
        cx = x0 + vx * f
        cy = y0 + vy * f
        boxes[f] = Box(f, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, label, **kw)
    return Track(tid, label, boxes)


def test_predict_linear_motion():
    # 10 frames: 0 to 9, vx=2.0, vy=1.0, x0=10, y0=20
    tr = mk_moving(1, range(10), x0=10.0, y0=20.0, vx=2.0, vy=1.0)
    # At frame 9: center is (10 + 2*9, 20 + 1*9) = (28, 29)
    # Predict with gap=5: frame 14 -> center should be (28 + 2*5, 29 + 1*5) = (38, 34)
    pred_x, pred_y = predict(tr, gap=5, k=5)
    assert abs(pred_x - 38.0) < 1e-4
    assert abs(pred_y - 34.0) < 1e-4


def test_predict_single_frame():
    tr = mk_moving(1, [0], x0=50.0, y0=60.0)
    pred_x, pred_y = predict(tr, gap=10)
    assert pred_x == 50.0 and pred_y == 60.0


def test_suggest_merges_basic():
    # Track moving continuously, split into A (frames 0..19) and B (frames 24..40) (gap = 5)
    tr_a = mk_moving(1, range(20), x0=0.0, y0=0.0, vx=1.0, vy=0.0)
    tr_b = mk_moving(2, range(24, 45), x0=0.0, y0=0.0, vx=1.0, vy=0.0)
    tracks = {1: tr_a, 2: tr_b}
    merges = suggest_merges(tracks, CFG)
    assert len(merges) == 1
    assert merges[0]["a"] == 1
    assert merges[0]["b"] == 2
    assert merges[0]["score"] > 0.7


def test_suggest_merges_different_labels():
    tr_a = mk_moving(1, range(20), label="pedestrian")
    tr_b = mk_moving(2, range(25, 45), label="car")
    tracks = {1: tr_a, 2: tr_b}
    merges = suggest_merges(tracks, CFG)
    assert merges == []


def test_suggest_merges_gap_too_large():
    tr_a = mk_moving(1, range(20))
    tr_b = mk_moving(2, range(60, 80))  # gap = 41 > 30
    tracks = {1: tr_a, 2: tr_b}
    merges = suggest_merges(tracks, CFG)
    assert merges == []


def test_suggest_merges_overlapping_time():
    tr_a = mk_moving(1, range(20))
    tr_b = mk_moving(2, range(15, 35))  # overlap
    tracks = {1: tr_a, 2: tr_b}
    merges = suggest_merges(tracks, CFG)
    assert merges == []


def test_suggest_merges_sorted_by_score():
    # Pair 1: perfectly matches motion
    a1 = mk_moving(1, range(20), x0=0, vx=1)
    b1 = mk_moving(2, range(22, 40), x0=0, vx=1)
    # Pair 2: slight displacement
    a2 = mk_moving(3, range(20), x0=100, y0=100, vx=1)
    b2 = mk_moving(4, range(22, 40), x0=115, y0=100, vx=1)
    tracks = {1: a1, 2: b1, 3: a2, 4: b2}
    merges = suggest_merges(tracks, CFG)
    assert len(merges) == 2
    assert merges[0]["score"] >= merges[1]["score"]


def test_top1_accuracy_on_injected_fragments():
    # Create multiple clean tracks moving in parallel
    clean = {
        i: mk_moving(i, range(0, 60), x0=i * 50.0, y0=0.0, vx=1.5, vy=0.5)
        for i in range(1, 10)
    }
    dirty, truths = inject_many(clean, n_per_type=3, seed=42)
    fragments = [x for x in truths if x["type"] == "fragment"]
    assert len(fragments) > 0

    merges = suggest_merges(dirty, CFG)
    pred_map = {m["a"]: m["b"] for m in merges}
    correct = sum(1 for f in fragments if pred_map.get(f["a"]) == f["b"])
    top1_acc = correct / len(fragments)
    # On synthetic clean lines, accuracy should be 100%
    assert top1_acc == 1.0

