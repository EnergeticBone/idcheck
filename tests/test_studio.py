import pytest
from studio.backend.orchestrator import demo, scan, quality


def test_studio_scan_demo():
    d = demo(None)
    res = scan(d)
    assert res["count"] > 0
    assert "boxes" in res["stats"]
    assert res["stats"]["boxes"] == 58
    # Should catch Box Jump (spike) and Gap Frame
    types = {i["type"] for i in res["issues"]}
    assert "Box Jump" in types
    assert "Gap Frame" in types


def test_studio_scan_invalid_box():
    d = demo(None)
    d["boxes"].append(dict(
        uid="inv_1",
        frame_id=3,
        track_id=99,
        x=10,
        y=10,
        w=-10,
        h=20,
        class_name="Car"
    ))
    res = scan(d)
    inv_issues = [i for i in res["issues"] if i["type"] == "Invalid Box"]
    assert len(inv_issues) == 1
    assert inv_issues[0]["track_id"] == 99
    assert inv_issues[0]["frame"] == 3


def test_studio_quality_gate():
    d = demo(None)
    # Demo has open high severity issues (jump, gap), so quality gate should not pass
    q = quality(d)
    assert "pass_gate" in q
    assert q["total_issues"] > 0
