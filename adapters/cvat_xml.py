import os
import xml.etree.ElementTree as ET
from core.schema import Box, Track


def _to_bool(val):
    if isinstance(val, bool):
        return val
    if not val:
        return False
    return str(val).strip().lower() in ("1", "true", "yes")


def _get_element_tree(source):
    if isinstance(source, ET.Element):
        return source
    if isinstance(source, str):
        if source.strip().startswith("<"):
            return ET.fromstring(source)
        if os.path.isfile(source):
            return ET.parse(source).getroot()
        # Maybe raw XML that doesn't start with '<' immediately (whitespace already stripped)
        try:
            return ET.fromstring(source)
        except ET.ParseError:
            if os.path.exists(source):
                return ET.parse(source).getroot()
            raise
    if hasattr(source, "read"):
        return ET.parse(source).getroot()
    raise TypeError(f"Không hỗ trợ định dạng nguồn: {type(source)}")


def load_cvat_xml_meta(source):
    """Đọc thông tin meta của CVAT XML (size, original_size, name, fps)."""
    root = _get_element_tree(source)
    meta = root.find("meta")
    info = {"width": None, "height": None, "size": None, "name": None, "fps": None}
    if meta is None:
        return info

    task_or_job = meta.find("task")
    if task_or_job is None:
        task_or_job = meta.find("job")
    if task_or_job is not None:
        name_el = task_or_job.find("name")
        if name_el is not None and name_el.text:
            info["name"] = name_el.text.strip()

        size_el = task_or_job.find("size")
        if size_el is not None and size_el.text:
            try:
                info["size"] = int(size_el.text.strip())
            except ValueError:
                pass

        orig_size_el = task_or_job.find("original_size")
        if orig_size_el is not None:
            w_el = orig_size_el.find("width")
            h_el = orig_size_el.find("height")
            if w_el is not None and w_el.text and h_el is not None and h_el.text:
                try:
                    info["width"] = int(w_el.text.strip())
                    info["height"] = int(h_el.text.strip())
                except ValueError:
                    pass

    return info


def _interpolate_track_boxes(boxes_dict, label):
    """Nội suy tuyến tính các frame giữa các keyframe nếu thiếu frame."""
    sorted_frames = sorted(boxes_dict.keys())
    if not sorted_frames:
        return {}

    all_boxes = {}
    for i in range(len(sorted_frames) - 1):
        f_start = sorted_frames[i]
        f_end = sorted_frames[i + 1]
        b_start = boxes_dict[f_start]
        b_end = boxes_dict[f_end]

        all_boxes[f_start] = b_start

        # Nếu hai frame cách nhau > 1 và b_start chưa ra ngoài
        if f_end > f_start + 1 and not b_start.outside:
            delta_f = f_end - f_start
            for f in range(f_start + 1, f_end):
                ratio = (f - f_start) / delta_f
                interp_x1 = b_start.x1 + ratio * (b_end.x1 - b_start.x1)
                interp_y1 = b_start.y1 + ratio * (b_end.y1 - b_start.y1)
                interp_x2 = b_start.x2 + ratio * (b_end.x2 - b_start.x2)
                interp_y2 = b_start.y2 + ratio * (b_end.y2 - b_start.y2)

                all_boxes[f] = Box(
                    frame=f,
                    x1=interp_x1,
                    y1=interp_y1,
                    x2=interp_x2,
                    y2=interp_y2,
                    label=label,
                    outside=False,
                    occluded=b_start.occluded,
                    keyframe=False,
                )

    last_f = sorted_frames[-1]
    all_boxes[last_f] = boxes_dict[last_f]
    return all_boxes


def load_cvat_xml(source, interpolate=True):
    """Đọc annotations từ CVAT for video 1.1 XML -> dict[int, Track].

    Hỗ trợ nội suy giữa các keyframe nếu interpolate=True.
    """
    root = _get_element_tree(source)
    tracks = {}

    for tr_el in root.findall("track"):
        tid = int(tr_el.attrib["id"])
        label = tr_el.attrib.get("label", "unknown")
        track = Track(id=tid, label=label)

        boxes = {}
        for b_el in tr_el.findall("box"):
            frame = int(b_el.attrib["frame"])
            xtl = float(b_el.attrib["xtl"])
            ytl = float(b_el.attrib["ytl"])
            xbr = float(b_el.attrib["xbr"])
            ybr = float(b_el.attrib["ybr"])
            outside = _to_bool(b_el.attrib.get("outside", 0))
            occluded = _to_bool(b_el.attrib.get("occluded", 0))
            keyframe = _to_bool(b_el.attrib.get("keyframe", 1))

            boxes[frame] = Box(
                frame=frame,
                x1=xtl,
                y1=ytl,
                x2=xbr,
                y2=ybr,
                label=label,
                outside=outside,
                occluded=occluded,
                keyframe=keyframe,
            )

        if interpolate and len(boxes) > 1:
            track.boxes = _interpolate_track_boxes(boxes, label)
        else:
            track.boxes = boxes

        tracks[tid] = track

    return tracks


def dump_cvat_xml(tracks, output_path, meta=None, zero_indexed=False):
    """Xuất dict[int, Track] ra định dạng CVAT for video 1.1 XML.

    Tham số:
        tracks: dict[int, Track]
        output_path: đường dẫn file .xml cần lưu
        meta: dict chứa 'name', 'size', 'width', 'height'
        zero_indexed: nếu True, dịch frame về 0-indexed (frame - 1 nếu min >= 1)
                      phù hợp khi import video trong CVAT đếm từ frame 0.
    """
    meta = meta or {}
    root = ET.Element("annotations")
    ET.SubElement(root, "version").text = "1.1"

    meta_el = ET.SubElement(root, "meta")
    task_el = ET.SubElement(meta_el, "task")
    if "name" in meta:
        ET.SubElement(task_el, "name").text = str(meta["name"])
    if "size" in meta:
        ET.SubElement(task_el, "size").text = str(meta["size"])
    if "width" in meta and "height" in meta:
        orig = ET.SubElement(task_el, "original_size")
        ET.SubElement(orig, "width").text = str(meta["width"])
        ET.SubElement(orig, "height").text = str(meta["height"])

    labels_el = ET.SubElement(task_el, "labels")
    unique_labels = sorted({tr.label for tr in tracks.values()} | {"pedestrian"})
    for l_name in unique_labels:
        lbl_el = ET.SubElement(labels_el, "label")
        ET.SubElement(lbl_el, "name").text = l_name
        ET.SubElement(lbl_el, "type").text = "any"

    all_frames = [f for tr in tracks.values() for f in tr.boxes]
    offset = 1 if (zero_indexed and all_frames and min(all_frames) >= 1) else 0

    total_size = meta.get("size")
    if not total_size and all_frames:
        total_size = max(all_frames) - offset + 1

    for tid in sorted(tracks):
        tr = tracks[tid]
        tr_el = ET.SubElement(root, "track", id=str(tid), label=tr.label)
        sorted_frames = sorted(tr.boxes)
        for f in sorted_frames:
            b = tr.boxes[f]
            box_frame = f - offset
            ET.SubElement(
                tr_el,
                "box",
                frame=str(box_frame),
                xtl=f"{b.x1:.2f}",
                ytl=f"{b.y1:.2f}",
                xbr=f"{b.x2:.2f}",
                ybr=f"{b.y2:.2f}",
                outside="1" if b.outside else "0",
                occluded="1" if b.occluded else "0",
                keyframe="1" if b.keyframe else "0",
            )

        # Đóng track bằng outside="1" để CVAT biết đối tượng đã rời khung hình,
        # tránh việc CVAT giữ box đứng yên ở mép màn hình đến hết video.
        if sorted_frames:
            last_f = sorted_frames[-1]
            last_b = tr.boxes[last_f]
            last_box_frame = last_f - offset
            if not last_b.outside and (total_size is None or last_box_frame + 1 < total_size):
                ET.SubElement(
                    tr_el,
                    "box",
                    frame=str(last_box_frame + 1),
                    xtl=f"{last_b.x1:.2f}",
                    ytl=f"{last_b.y1:.2f}",
                    xbr=f"{last_b.x2:.2f}",
                    ybr=f"{last_b.y2:.2f}",
                    outside="1",
                    occluded="0",
                    keyframe="1",
                )

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

