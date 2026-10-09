from core.schema import Box, Track
from core.inject import split_track, delete_frames, swap_ids, inject_many


def mk(tid, f0, f1, x0=0.0, label="pedestrian"):
    return Track(tid, label, {f: Box(f, x0 + f, 0, x0 + f + 10, 20, label)
                              for f in range(f0, f1)})


def test_split():
    tr = {1: mk(1, 0, 40)}
    truth = split_track(tr, 1, at=20, drop=3)
    assert truth["type"] == "fragment" and truth["frame"] == 19
    assert max(tr[1].boxes) == 19 and min(tr[truth["b"]].boxes) == 23


def test_gap():
    tr = {1: mk(1, 0, 40)}
    truth = delete_frames(tr, 1, at=10, k=3)
    assert truth["frame"] == 9
    assert 10 not in tr[1].boxes and 13 in tr[1].boxes


def test_swap():
    tr = {1: mk(1, 0, 40, x0=0), 2: mk(2, 0, 40, x0=100)}
    swap_ids(tr, 1, 2, t=20)
    assert tr[1].boxes[25].x1 == 125 and tr[2].boxes[25].x1 == 25


def test_inject_many_is_reproducible():
    clean = {i: mk(i, 0, 60, x0=10 * i) for i in range(1, 30)}
    _, t1 = inject_many(clean, 3, seed=1)
    _, t2 = inject_many(clean, 3, seed=1)
    assert t1 == t2 and len(t1) > 0