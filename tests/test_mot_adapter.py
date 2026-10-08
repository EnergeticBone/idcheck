from adapters.mot import load_mot_gt


def test_load_basic(tmp_path):
    p = tmp_path / "gt.txt"
    p.write_text(
        "1,1,10,20,30,40,1,1,1.0\n"
        "2,1,12,20,30,40,1,1,0.3\n"
        "2,2,50,60,10,10,0,1,1.0\n"   # conf=0 -> bỏ
        "3,3,5,5,10,10,1,2,1.0\n"     # class 2 -> bỏ
    )
    tracks = load_mot_gt(str(p))
    assert set(tracks) == {1}
    assert sorted(tracks[1].boxes) == [1, 2]
    b = tracks[1].boxes[1]
    assert (b.x1, b.y1, b.x2, b.y2) == (10, 20, 40, 60)
    assert tracks[1].boxes[2].occluded is True
    assert tracks[1].start == 1 and tracks[1].end == 2