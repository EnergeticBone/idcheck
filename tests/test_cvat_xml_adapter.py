import pytest
from core.schema import Box, Track
from adapters.cvat_xml import load_cvat_xml, load_cvat_xml_meta


SAMPLE_CVAT_XML_DENSE = """<?xml version="1.0" encoding="utf-8"?>
<annotations>
  <version>1.1</version>
  <meta>
    <task>
      <id>42</id>
      <name>test_video</name>
      <size>50</size>
      <original_size>
        <width>1920</width>
        <height>1080</height>
      </original_size>
    </task>
  </meta>
  <track id="1" label="pedestrian">
    <box frame="0" xtl="100.0" ytl="200.0" xbr="150.0" ybr="300.0" outside="0" occluded="0" keyframe="1" />
    <box frame="1" xtl="110.0" ytl="200.0" xbr="160.0" ybr="300.0" outside="0" occluded="0" keyframe="0" />
    <box frame="2" xtl="120.0" ytl="200.0" xbr="170.0" ybr="300.0" outside="0" occluded="1" keyframe="0" />
    <box frame="3" xtl="130.0" ytl="200.0" xbr="180.0" ybr="300.0" outside="1" occluded="0" keyframe="1" />
  </track>
</annotations>
"""

SAMPLE_CVAT_XML_KEYFRAMES = """<?xml version="1.0" encoding="utf-8"?>
<annotations>
  <version>1.1</version>
  <meta>
    <job>
      <id>10</id>
      <size>100</size>
      <original_size>
        <width>1280</width>
        <height>720</height>
      </original_size>
    </job>
  </meta>
  <track id="5" label="car">
    <box frame="10" xtl="100.0" ytl="100.0" xbr="200.0" ybr="200.0" outside="0" occluded="0" keyframe="1" />
    <box frame="14" xtl="140.0" ytl="100.0" xbr="240.0" ybr="200.0" outside="0" occluded="0" keyframe="1" />
    <box frame="16" xtl="160.0" ytl="100.0" xbr="260.0" ybr="200.0" outside="1" occluded="0" keyframe="1" />
  </track>
</annotations>
"""


def test_load_cvat_xml_dense():
    tracks = load_cvat_xml(SAMPLE_CVAT_XML_DENSE)
    assert len(tracks) == 1
    assert 1 in tracks
    tr = tracks[1]
    assert tr.id == 1
    assert tr.label == "pedestrian"
    assert len(tr.boxes) == 4

    b0 = tr.boxes[0]
    assert b0.x1 == 100.0 and b0.y1 == 200.0
    assert not b0.occluded and not b0.outside and b0.keyframe

    b2 = tr.boxes[2]
    assert b2.occluded and not b2.outside and not b2.keyframe

    b3 = tr.boxes[3]
    assert b3.outside and b3.keyframe


def test_load_cvat_xml_interpolation():
    tracks = load_cvat_xml(SAMPLE_CVAT_XML_KEYFRAMES, interpolate=True)
    assert 5 in tracks
    tr = tracks[5]
    assert tr.id == 5
    assert tr.label == "car"

    # Frames 10 to 14 should have interpolated frames 11, 12, 13
    assert 10 in tr.boxes
    assert 11 in tr.boxes
    assert 12 in tr.boxes
    assert 13 in tr.boxes
    assert 14 in tr.boxes

    # Frame 12 is halfway between 10 (xtl=100) and 14 (xtl=140) -> xtl=120
    b12 = tr.boxes[12]
    assert b12.x1 == pytest.approx(120.0)
    assert b12.x2 == pytest.approx(220.0)
    assert not b12.keyframe
    assert not b12.outside

    # Interpolation towards outside keyframe at 16:
    # 15 should be interpolated between 14 and 16
    assert 15 in tr.boxes
    b15 = tr.boxes[15]
    assert b15.x1 == pytest.approx(150.0)
    assert not b15.outside

    # Frame 16 should be outside
    assert 16 in tr.boxes
    assert tr.boxes[16].outside


def test_load_cvat_xml_meta():
    meta1 = load_cvat_xml_meta(SAMPLE_CVAT_XML_DENSE)
    assert meta1["width"] == 1920
    assert meta1["height"] == 1080
    assert meta1["size"] == 50
    assert meta1["name"] == "test_video"

    meta2 = load_cvat_xml_meta(SAMPLE_CVAT_XML_KEYFRAMES)
    assert meta2["width"] == 1280
    assert meta2["height"] == 720
    assert meta2["size"] == 100


def test_dump_cvat_xml(tmp_path):
    from adapters.cvat_xml import dump_cvat_xml

    tr = Track(id=1, label="pedestrian")
    tr.boxes[0] = Box(0, 10.0, 20.0, 30.0, 40.0, "pedestrian")
    tr.boxes[1] = Box(1, 15.0, 20.0, 35.0, 40.0, "pedestrian", occluded=True)

    out_file = str(tmp_path / "test_dump.xml")
    dump_cvat_xml({1: tr}, out_file, meta={"name": "demo", "size": 2, "width": 1920, "height": 1080})

    # Read back and verify
    reloaded = load_cvat_xml(out_file)
    assert 1 in reloaded
    assert len(reloaded[1].boxes) == 2
    assert reloaded[1].boxes[1].occluded

