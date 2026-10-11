from unittest.mock import MagicMock, patch
import pytest
from core.schema import Box, Track
from adapters.cvat_api import (
    load_job_tracks,
    post_alerts_as_issues,
    format_alert_message,
)


def _make_mock_tracked_shape(frame, points, outside=False, occluded=False):
    shape = MagicMock()
    shape.frame = frame
    shape.points = points
    shape.outside = outside
    shape.occluded = occluded
    return shape


def _make_mock_labeled_track(track_id, label_id, shapes):
    lt = MagicMock()
    lt.id = track_id
    lt.label_id = label_id
    lt.shapes = shapes
    return lt


def test_load_job_tracks():
    mock_client = MagicMock()
    mock_job = MagicMock()
    mock_client.jobs.retrieve.return_value = mock_job

    label_ped = MagicMock()
    label_ped.id = 1
    label_ped.name = "pedestrian"
    mock_job.get_labels.return_value = [label_ped]

    s0 = _make_mock_tracked_shape(0, [10.0, 20.0, 30.0, 40.0], outside=False)
    s4 = _make_mock_tracked_shape(4, [50.0, 20.0, 70.0, 40.0], outside=False)
    s6 = _make_mock_tracked_shape(6, [70.0, 20.0, 90.0, 40.0], outside=True)

    track1 = _make_mock_labeled_track(101, 1, [s0, s4, s6])
    mock_annotations = MagicMock()
    mock_annotations.tracks = [track1]
    mock_job.get_annotations.return_value = mock_annotations

    tracks, meta = load_job_tracks(mock_client, job_id=12, interpolate=True)

    assert 101 in tracks
    tr = tracks[101]
    assert tr.label == "pedestrian"
    assert tr.id == 101

    # Keyframes and interpolated frames
    assert 0 in tr.boxes
    assert 1 in tr.boxes
    assert 2 in tr.boxes
    assert 3 in tr.boxes
    assert 4 in tr.boxes
    assert 5 in tr.boxes
    assert 6 in tr.boxes

    # Frame 2 is halfway between frame 0 (x1=10) and frame 4 (x1=50) -> x1=30
    assert tr.boxes[2].x1 == pytest.approx(30.0)
    assert not tr.boxes[2].outside

    # Frame 6 is outside
    assert tr.boxes[6].outside


def test_format_alert_message():
    alert_jump = {"type": "jump", "track_id": 5, "frame": 12, "detail": "dịch 1.5 lần"}
    msg = format_alert_message(alert_jump)
    assert "[JUMP]" in msg
    assert "Track #5" in msg
    assert "dịch 1.5 lần" in msg

    alert_switch = {"type": "switch", "track_id": 3, "other_id": 8, "frame": 20, "detail": "nghi hoán ID"}
    msg2 = format_alert_message(alert_switch)
    assert "[SWITCH]" in msg2
    assert "Track #3 & #8" in msg2


def test_post_alerts_as_issues():
    mock_client = MagicMock()
    mock_job = MagicMock()
    mock_client.jobs.retrieve.return_value = mock_job
    mock_job.get_issues.return_value = []

    # Mock track
    tr = Track(id=5, label="pedestrian")
    tr.boxes[10] = Box(10, 100.0, 200.0, 150.0, 250.0, "pedestrian")
    tracks = {5: tr}

    alerts = [
        {"type": "jump", "track_id": 5, "frame": 10, "detail": "dịch lớn"},
    ]

    results = post_alerts_as_issues(mock_client, job_id=12, alerts=alerts, tracks=tracks, dry_run=False)

    assert len(results) == 1
    assert results[0]["status"] == "created"
    assert mock_client.issues.create.call_count == 1
    call_arg = mock_client.issues.create.call_args[0][0]
    assert call_arg["frame"] == 10
    assert call_arg["job"] == 12
    assert call_arg["position"] == [100.0, 200.0, 150.0, 250.0]
    assert "[JUMP]" in call_arg["message"]


def test_post_alerts_skip_duplicates():
    mock_client = MagicMock()
    mock_job = MagicMock()
    mock_client.jobs.retrieve.return_value = mock_job

    # Existing issue at frame 10 for track 5
    existing_issue = MagicMock()
    existing_issue.frame = 10
    existing_comment = MagicMock()
    existing_comment.message = "[JUMP] Track #5: dịch lớn"
    existing_issue.get_comments.return_value = [existing_comment]
    mock_job.get_issues.return_value = [existing_issue]

    tr = Track(id=5, label="pedestrian")
    tr.boxes[10] = Box(10, 100.0, 200.0, 150.0, 250.0, "pedestrian")
    tracks = {5: tr}

    alerts = [
        {"type": "jump", "track_id": 5, "frame": 10, "detail": "dịch lớn"},
    ]

    results = post_alerts_as_issues(mock_client, job_id=12, alerts=alerts, tracks=tracks, dry_run=False, skip_duplicates=True)

    assert len(results) == 1
    assert results[0]["status"] == "skipped"
    assert mock_client.issues.create.call_count == 0


def test_check_job_orchestration():
    from adapters.cvat_api import check_job

    mock_client = MagicMock()
    mock_job = MagicMock()
    mock_client.jobs.retrieve.return_value = mock_job
    mock_job.get_labels.return_value = []
    mock_job.get_issues.return_value = []

    mock_annotations = MagicMock()
    mock_annotations.tracks = []
    mock_job.get_annotations.return_value = mock_annotations

    res = check_job(mock_client, job_id=1, cfg={}, create_issues=False)
    assert res["job_id"] == 1
    assert res["num_tracks"] == 0
    assert res["alerts"] == []
    assert res["merges"] == []

