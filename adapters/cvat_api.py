import os
import re
import logging
import urllib3
from cvat_sdk.core.client import Client, Config
from core.schema import Box, Track
from core.rules import run_rules
from core.merge import suggest_merges
from adapters.cvat_xml import _interpolate_track_boxes

logger = logging.getLogger(__name__)


def _load_env():
    env_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    for p in (env_file, ".env"):
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v
            break


def get_client(host=None, user=None, password=None, insecure=None):
    """Khởi tạo CVAT client từ tham số hoặc biến môi trường."""
    _load_env()
    host = host or os.environ.get("CVAT_HOST", "http://localhost:8080")
    user = user or os.environ.get("CVAT_USER", "")
    password = password or os.environ.get("CVAT_PASS", "")
    if not user or not password:
        raise ValueError("Thiếu thông tin xác thực CVAT (CVAT_USER, CVAT_PASS)")

    if insecure is None:
        insecure = os.environ.get("CVAT_INSECURE", "1").lower() in ("1", "true", "yes")

    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        config = Config(verify_ssl=False)
    else:
        config = None

    client = Client(url=host, config=config)
    client.login((user, password))
    return client


def load_job_tracks(client, job_id, interpolate=True, use_client_id=True):
    """Lấy annotations của job qua API -> (dict[int, Track], meta_info).

    use_client_id: Nếu True (mặc định), gán Track ID là Client ID hiển thị trên
                   giao diện CVAT (1, 2, ... N) để mã Track trong Issue trùng khớp
                   100% với số hiển thị trên màn hình CVAT.
    """
    job = client.jobs.retrieve(job_id)
    labels = {lbl.id: lbl.name for lbl in job.get_labels()}

    frame_size = None
    try:
        jmeta = job.get_meta()
        frames = jmeta.get("frames", [])
        if frames and "width" in frames[0] and "height" in frames[0]:
            frame_size = (int(frames[0]["width"]), int(frames[0]["height"]))
    except Exception:
        pass

    meta = {
        "job_id": job_id,
        "task_id": getattr(job, "task_id", None),
        "stage": getattr(job, "stage", None),
        "state": getattr(job, "state", None),
        "frame_size": frame_size,
        "server_to_client_id": {},
        "client_to_server_id": {},
    }

    annotations = job.get_annotations()
    tracks = {}

    for idx, lt in enumerate(getattr(annotations, "tracks", [])):
        client_id = idx + 1
        tid = client_id if use_client_id else lt.id
        meta["server_to_client_id"][lt.id] = client_id
        meta["client_to_server_id"][client_id] = lt.id

        label_id = lt.label_id
        label_name = labels.get(label_id, str(label_id))
        track_attrs = []
        if hasattr(lt, "attributes") and lt.attributes:
            for a in lt.attributes:
                if hasattr(a, "spec_id") and hasattr(a, "value"):
                    track_attrs.append({"spec_id": a.spec_id, "value": a.value})
                elif isinstance(a, dict):
                    track_attrs.append(a)

        track = Track(id=tid, label=label_name, attributes=track_attrs)

        boxes = {}
        for shape in lt.shapes:
            f = int(shape.frame)
            pts = shape.points
            if len(pts) >= 4:
                x1, y1, x2, y2 = float(pts[0]), float(pts[1]), float(pts[2]), float(pts[3])
            else:
                x1, y1, x2, y2 = 0.0, 0.0, 0.0, 0.0

            outside = bool(shape.outside)
            occluded = bool(shape.occluded)

            shape_attrs = []
            if hasattr(shape, "attributes") and shape.attributes:
                for a in shape.attributes:
                    if hasattr(a, "spec_id") and hasattr(a, "value"):
                        shape_attrs.append({"spec_id": a.spec_id, "value": a.value})
                    elif isinstance(a, dict):
                        shape_attrs.append(a)

            boxes[f] = Box(
                frame=f,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                label=label_name,
                outside=outside,
                occluded=occluded,
                keyframe=True,
                attributes=shape_attrs,
            )

        if interpolate and len(boxes) > 1:
            track.boxes = _interpolate_track_boxes(boxes, label_name)
        else:
            track.boxes = boxes

        tracks[tid] = track

    return tracks, meta


def format_alert_message(alert):
    """Format nội dung tin nhắn alert theo chuẩn rõ ràng."""
    type_str = alert.get("type", "alert").upper()
    tid = alert.get("track_id")
    other = alert.get("other_id")
    detail = str(alert.get("detail", "")).strip()

    # Làm sạch tiền tố trùng lặp nếu detail đã được format trước đó
    detail = re.sub(r'^(?:\[CVAT_ISSUE\]\s*(?:Track:?\s*)?)+', '', detail, flags=re.IGNORECASE).strip()

    # Nếu detail đã có tiền tố chuẩn [TYPE] Track #... thì tránh bọc lặp lại
    if detail.startswith(f"[{type_str}]"):
        return detail

    if other is not None and other != tid:
        target = f"Track #{tid} & #{other}"
    elif tid is not None:
        target = f"Track #{tid}"
    else:
        target = "Track"

    return f"[{type_str}] {target}: {detail}"


def _get_alert_box_position(alert, tracks):
    """Tìm bounding box [x1, y1, x2, y2] phù hợp nhất tại frame lỗi."""
    f = alert.get("frame", 0)
    tid = alert.get("track_id")
    if tid is not None:
        try:
            tid = int(tid)
        except (ValueError, TypeError):
            pass
    if tid is None and alert.get("detail"):
        m = re.search(r'Track\s*#?(\d+)', str(alert.get("detail", "")))
        if m:
            try:
                tid = int(m.group(1))
            except ValueError:
                pass

    if tracks and tid is not None:
        # Hỗ trợ cả key int lẫn str trong dict tracks
        tr = tracks.get(tid) or tracks.get(str(tid))
        if tr and getattr(tr, "boxes", None):
            if f in tr.boxes:
                b = tr.boxes[f]
                return [round(b.x1, 1), round(b.y1, 1), round(b.x2, 1), round(b.y2, 1)]
            nearest_f = min(tr.boxes.keys(), key=lambda frame: abs(frame - f))
            b = tr.boxes[nearest_f]
            return [round(b.x1, 1), round(b.y1, 1), round(b.x2, 1), round(b.y2, 1)]
    return [0.0, 0.0, 100.0, 100.0]


def post_alerts_as_issues(client, job_id, alerts, tracks=None, dry_run=False, skip_duplicates=True):
    """Tạo CVAT issues trên job từ danh sách alerts của rule checker."""
    job = client.jobs.retrieve(job_id)

    existing_signatures = set()
    if skip_duplicates:
        try:
            existing_issues = job.get_issues()
            for iss in existing_issues:
                frame_no = getattr(iss, "frame", None)
                msg_texts = []
                if hasattr(iss, "get_comments"):
                    try:
                        msg_texts = [c.message for c in iss.get_comments() if hasattr(c, "message")]
                    except Exception:
                        pass
                for text in msg_texts:
                    existing_signatures.add((frame_no, text.strip()))
        except Exception as e:
            logger.warning("Không thể lấy danh sách issues hiện tại: %s", e)

    results = []
    for alert in alerts:
        frame = alert["frame"]
        message = format_alert_message(alert)
        sig = (frame, message.strip())

        if skip_duplicates and sig in existing_signatures:
            results.append({
                "alert": alert,
                "status": "skipped",
                "reason": "duplicate_issue",
                "message": message,
            })
            continue

        position = _get_alert_box_position(alert, tracks)

        if dry_run:
            results.append({
                "alert": alert,
                "status": "dry_run",
                "frame": frame,
                "position": position,
                "message": message,
            })
            continue

        try:
            client.issues.create({
                "frame": frame,
                "position": position,
                "job": job_id,
                "message": message,
            })
            existing_signatures.add(sig)
            results.append({
                "alert": alert,
                "status": "created",
                "frame": frame,
                "position": position,
                "message": message,
            })
        except Exception as exc:
            logger.error("Lỗi khi tạo issue tại frame %d: %s", frame, exc)
            results.append({
                "alert": alert,
                "status": "error",
                "error": str(exc),
                "frame": frame,
                "message": message,
            })

    return results


def post_merges_as_issues(client, job_id, merges, tracks=None, dry_run=False, skip_duplicates=True):
    """Tạo CVAT issues gợi ý nối (merge) các track bị đứt."""
    alerts = []
    for m in merges:
        ta, tb = m["a"], m["b"]
        score = m["score"]
        gap = m["gap"]
        frame = tracks[ta].end if tracks and ta in tracks else 0
        alerts.append({
            "type": "merge_suggestion",
            "track_id": ta,
            "other_id": tb,
            "frame": frame,
            "detail": f"gợi ý nối với track {tb} (gap {gap} frames, score {score})",
        })
    return post_alerts_as_issues(
        client, job_id, alerts, tracks=tracks, dry_run=dry_run, skip_duplicates=skip_duplicates
    )


def check_job(client, job_id, cfg, create_issues=False, dry_run=False, include_merges=True, min_score=0.5):
    """Kiểm tra toàn diện một job CVAT: tải tracks, chạy rules, tìm merge và tùy chọn tạo issue."""
    tracks, meta = load_job_tracks(client, job_id, interpolate=True)
    alerts = run_rules(tracks, cfg, frame_size=meta.get("frame_size"))
    merges = suggest_merges(tracks, cfg, min_score=min_score, frame_size=meta.get("frame_size"))

    issue_results = []
    if create_issues or dry_run:
        issue_results.extend(post_alerts_as_issues(client, job_id, alerts, tracks=tracks, dry_run=dry_run))
        if include_merges:
            issue_results.extend(post_merges_as_issues(client, job_id, merges, tracks=tracks, dry_run=dry_run))

    return {
        "job_id": job_id,
        "meta": meta,
        "num_tracks": len(tracks),
        "alerts": alerts,
        "merges": merges,
        "issue_results": issue_results,
    }

