import os
import json
import tempfile
from unittest.mock import MagicMock, patch
import pytest
from cli import build_parser, main


SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<annotations>
  <version>1.1</version>
  <meta>
    <task>
      <size>30</size>
      <original_size><width>1920</width><height>1080</height></original_size>
    </task>
  </meta>
  <track id="1" label="pedestrian">
    <box frame="0" xtl="100.0" ytl="100.0" xbr="150.0" ybr="200.0" outside="0" occluded="0" keyframe="1" />
    <box frame="5" xtl="500.0" ytl="500.0" xbr="550.0" ybr="600.0" outside="0" occluded="0" keyframe="1" />
  </track>
</annotations>
"""


def test_cli_check_xml_stdout(capsys):
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
        f.write(SAMPLE_XML)
        f_path = f.name

    try:
        parser = build_parser()
        args = parser.parse_args(["check-xml", f_path])
        args.func(args)

        captured = capsys.readouterr()
        assert "KẾT QUẢ KIỂM TRA IDCHECK" in captured.out
        assert "Thống kê cảnh báo lỗi:" in captured.out
    finally:
        os.remove(f_path)


def test_cli_check_xml_json(capsys):
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
        f.write(SAMPLE_XML)
        f_path = f.name

    try:
        parser = build_parser()
        args = parser.parse_args(["check-xml", f_path, "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "alerts" in data
        assert "merges" in data
        assert data["num_tracks"] == 1
    finally:
        os.remove(f_path)


@patch("cli.check_job")
@patch("cli.get_client")
def test_cli_check_job(mock_get_client, mock_check_job, capsys):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_check_job.return_value = {
        "job_id": 99,
        "num_tracks": 2,
        "alerts": [{"type": "jump", "track_id": 1, "frame": 10, "detail": "dịch lớn"}],
        "merges": [],
        "issue_results": [{"status": "created"}],
    }

    parser = build_parser()
    args = parser.parse_args(["check-job", "99", "--create-issues"])
    args.func(args)

    captured = capsys.readouterr()
    assert "Kiểm tra Job #99" in captured.out
    assert "JUMP" in captured.out
    assert "Đã tạo mới: 1" in captured.out


@patch("cli.check_job")
@patch("cli.get_client")
def test_cli_watch_once(mock_get_client, mock_check_job, capsys):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    job_mock = MagicMock()
    job_mock.id = 55
    job_mock.stage = "validation"
    job_mock.updated_date = "2026-10-10T12:00:00Z"
    mock_client.jobs.list.return_value = [job_mock]

    mock_check_job.return_value = {
        "job_id": 55,
        "num_tracks": 1,
        "alerts": [],
        "merges": [],
        "issue_results": [],
    }

    parser = build_parser()
    args = parser.parse_args(["watch", "--once", "--dry-run"])
    args.func(args)

    mock_check_job.assert_called_once()

