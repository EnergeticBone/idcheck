import pytest
from studio.backend.benchmark import normalize_gt, _score, _match
from studio.backend.cvat_workflow import _keep_keyframes, boxes_to_cvat


def test_normalize_gt():
    # Team format
    team_events = [
        {'type': 'switch', 'a': 1, 'b': 2, 'frame': 15},
        {'type': 'gap', 'a': 3, 'frame': 20},
        {'type': 'fragment', 'a': 4, 'b': 5, 'frame': 30}
    ]
    res = normalize_gt(team_events)
    assert len(res) == 3
    assert res[0]['type'] == 'ID Switch Candidate'
    assert res[0]['frame'] == 15
    assert set(res[0]['tracks']) == {1, 2}
    assert res[1]['type'] == 'Gap Frame'
    assert res[1]['frame'] == 20
    assert res[2]['type'] == 'ID Switch Candidate'


def test_score_and_match():
    pred = [
        {'type': 'ID Switch Candidate', 'frame': 16, 'track_id': 1, 'pair': [2]},
        {'type': 'Gap Frame', 'frame': 21, 'track_id': 3}
    ]
    gt = [
        {'type': 'ID Switch Candidate', 'frame': 15, 'track_id': 1, 'tracks': [1, 2], 'frame_to': 15},
        {'type': 'Gap Frame', 'frame': 20, 'track_id': 3, 'tracks': [3], 'frame_to': 22}
    ]
    sc = _score(pred, gt, tol=2)
    assert sc['tp'] == 2
    assert sc['fp'] == 0
    assert sc['fn'] == 0
    assert sc['precision'] == 1.0
    assert sc['recall'] == 1.0
    assert sc['f1'] == 1.0
    assert sc['target_met'] is True


def test_keep_keyframes_rdp():
    # Linear movement from frame 0 to frame 10: intermediate keyframes should be decimated!
    points = [
        (0, [0.0, 0.0, 10.0, 10.0]),
        (2, [2.0, 2.0, 12.0, 12.0]),
        (5, [5.0, 5.0, 15.0, 15.0]),
        (8, [8.0, 8.0, 18.0, 18.0]),
        (10, [10.0, 10.0, 20.0, 20.0]),
    ]
    kept = _keep_keyframes(points, tolerance=0.5)
    # Only endpoints (0 and 4) need to be kept because intermediate points are perfectly collinear!
    assert kept == [0, 4]


def test_boxes_to_cvat():
    boxes = [
        {'frame_id': 0, 'track_id': 1, 'x': 10, 'y': 10, 'w': 20, 'h': 20, 'class_name': 'Car'},
        {'frame_id': 1, 'track_id': 1, 'x': 12, 'y': 12, 'w': 20, 'h': 20, 'class_name': 'Car'},
        {'frame_id': 2, 'track_id': 1, 'x': 14, 'y': 14, 'w': 20, 'h': 20, 'class_name': 'Car'},
    ]
    label_ids = {'Car': 101}
    payload = boxes_to_cvat(boxes, label_ids, stop_frame=10)
    assert len(payload['tracks']) == 1
    t = payload['tracks'][0]
    assert t['label_id'] == 101
    # Check that an outside keyframe was added at frame 3
    shapes = t['shapes']
    assert any(s['outside'] is True and s['frame'] == 3 for s in shapes)


def test_absent_class_flip():
    from studio.backend.benchmark import _is_absent_class_flip
    boxes = [
        {'frame_id': 0, 'track_id': 1, 'x': 10, 'y': 10, 'w': 20, 'h': 20, 'class_name': 'Car'},
        {'frame_id': 1, 'track_id': 1, 'x': 12, 'y': 12, 'w': 20, 'h': 20, 'class_name': 'Car'},
    ]
    gt_absent = {'type': 'Class Change', 'track_id': 1, 'frame': 0, 'planted': 'class_flip 1'}
    # Track 1 has only 1 class ('Car'), so the class flip is absent (lost during CVAT XML export)
    assert _is_absent_class_flip(gt_absent, boxes) is True

    # If track actually had different classes, it is not absent
    boxes_with_flip = [
        {'frame_id': 0, 'track_id': 1, 'x': 10, 'y': 10, 'w': 20, 'h': 20, 'class_name': 'Car'},
        {'frame_id': 1, 'track_id': 1, 'x': 12, 'y': 12, 'w': 20, 'h': 20, 'class_name': 'Pedestrian'},
    ]
    assert _is_absent_class_flip(gt_absent, boxes_with_flip) is False

