"""CVAT Integration Workflow for idcheck Studio.

Enables seamless QC pipeline between CVAT and idcheck Studio:
- Lead assigns Project / Task / Job to Annotator & Reviewer.
- Annotators view assigned jobs in 'annotation' stage; submit to 'validation'.
- Reviewers view jobs in 'validation' stage; scan issues automatically with idcheck core rules.
- Reviewers audit issues, reject & raise issues directly onto CVAT (stage -> 'annotation', state -> 'rejected')
  or approve (stage -> 'acceptance', state -> 'completed').
"""
import io
import json
import logging
import os
from pathlib import Path
import threading
import time

from adapters.cvat_api import (
    get_client,
    load_job_tracks,
    post_alerts_as_issues,
    post_merges_as_issues,
)
from core.config import resolve_cfg
from studio.backend.orchestrator import scan, _load_idcheck_cfg
from studio.backend.auth import require

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
FRAME_CACHE_DIR = ROOT / "storage" / "cvat_frames"
FRAME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
META_FILE = ROOT / "storage" / "cvat_job_meta.json"

LOCK = threading.RLock()
_CLIENT_CACHE = {"client": None, "timestamp": 0}
CACHE_TTL = 1800  # 30 minutes


def _load_job_meta():
    if not META_FILE.is_file():
        return {}
    try:
        with META_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_job_meta(meta):
    tmp = META_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(META_FILE)


def _get_active_client():
    now = time.time()
    with LOCK:
        if _CLIENT_CACHE["client"] is not None and (now - _CLIENT_CACHE["timestamp"]) < CACHE_TTL:
            return _CLIENT_CACHE["client"]
        try:
            client = get_client()
            _CLIENT_CACHE["client"] = client
            _CLIENT_CACHE["timestamp"] = now
            return client
        except Exception as e:
            logger.error("Failed to connect to CVAT: %s", e)
            raise ConnectionError(f"Không thể kết nối đến máy chủ CVAT: {e}")


def list_jobs(actor):
    """Lấy danh sách CVAT jobs kèm thông tin project, task, assignee và lọc theo vai trò."""
    client = _get_active_client()
    meta_all = _load_job_meta()

    # Lấy thông tin Projects và Tasks để hiển thị tên thân thiện
    try:
        projects = {p.id: p.name for p in client.projects.list()}
    except Exception:
        projects = {}

    try:
        tasks = {t.id: {"name": t.name, "project_id": getattr(t, "project_id", None)} for t in client.tasks.list()}
    except Exception:
        tasks = {}

    try:
        raw_jobs = client.jobs.list()
    except Exception as e:
        raise RuntimeError(f"Lỗi khi lấy danh sách jobs từ CVAT: {e}")

    host = getattr(client, "url", os.environ.get("CVAT_HOST", "https://cvat.trantuli.id.vn")).rstrip("/")
    all_jobs = []

    for j in raw_jobs:
        t_info = tasks.get(j.task_id, {})
        p_id = t_info.get("project_id")
        p_name = projects.get(p_id, f"Project #{p_id}" if p_id else "No Project")
        t_name = t_info.get("name", f"Task #{j.task_id}")

        assignee_name = j.assignee.username if getattr(j, "assignee", None) else None
        assignee_id = j.assignee.id if getattr(j, "assignee", None) else None

        job_meta = meta_all.get(str(j.id), {})
        annotator_name = job_meta.get("annotator_name")
        reviewer_name = job_meta.get("reviewer_name")

        # Nếu job đang ở annotation mà chưa có annotator trong meta, lấy assignee hiện tại
        if not annotator_name and j.stage == "annotation" and assignee_name:
            annotator_name = assignee_name

        start_f = getattr(j, "start_frame", 0)
        stop_f = getattr(j, "stop_frame", 0)
        frame_cnt = getattr(j, "frame_count", 0) or (stop_f - start_f + 1 if stop_f >= start_f else 0)

        all_jobs.append({
            "id": j.id,
            "task_id": j.task_id,
            "task_name": t_name,
            "project_name": p_name,
            "stage": getattr(j, "stage", "annotation"),
            "state": getattr(j, "state", "new"),
            "assignee": assignee_name,
            "assignee_id": assignee_id,
            "annotator_name": annotator_name,
            "reviewer_name": reviewer_name,
            "frame_count": frame_cnt,
            "start_frame": start_f,
            "stop_frame": stop_f,
            "cvat_url": f"{host}/tasks/{j.task_id}/jobs/{j.id}",
        })

    # Sắp xếp theo ID giảm dần (mới nhất lên trước)
    all_jobs.sort(key=lambda x: x["id"], reverse=True)

    role = actor.get("role", "annotator")
    user_name = actor.get("username", "")

    # Phân quyền lọc danh sách
    filtered_jobs = []
    if role == "annotator":
        # Annotator: Chỉ phụ trách các job ở giai đoạn annotation (việc được gán hoặc chưa gán; việc mới hoặc bị trả lại sửa)
        for j in all_jobs:
            is_assigned_to_me = j["assignee"] and j["assignee"].lower() == user_name.lower()
            is_unassigned = not j["assignee"]
            if (is_assigned_to_me or is_unassigned) and j["stage"] == "annotation":
                filtered_jobs.append(j)
    elif role == "reviewer":
        # Reviewer: Xem tất cả jobs ở stage validation (cần QC), jobs đã trả lại (rejected), hoặc đã nghiệm thu
        for j in all_jobs:
            if j["stage"] in ("validation", "acceptance") or j.get("state") in ("rejected", "completed"):
                filtered_jobs.append(j)
        # Nếu chưa có job validation nào, trả về danh sách để reviewer thấy tổng quan
        if not filtered_jobs:
            filtered_jobs = all_jobs
    else:
        # Admin / Lead: Xem toàn bộ
        filtered_jobs = all_jobs

    return {
        "jobs": filtered_jobs,
        "all_count": len(all_jobs),
        "filtered_count": len(filtered_jobs),
        "role": role,
        "username": user_name,
        "cvat_host": host,
    }


def list_cvat_users(actor):
    """Danh sách người dùng trên CVAT kèm vai trò đã phân quyền để Lead gán việc và Annotator chọn Reviewer."""
    from studio.backend.auth import _load_cvat_roles
    client = _get_active_client()
    saved_roles = _load_cvat_roles()
    try:
        users = client.users.list()
        out = []
        for u in users:
            is_staff = getattr(u, "is_staff", False)
            if is_staff:
                role = "admin"
            elif u.username in saved_roles:
                role = saved_roles[u.username]
            else:
                role = "annotator"
            out.append({
                "id": u.id,
                "username": u.username,
                "role": role,
                "is_staff": is_staff,
                "is_active": getattr(u, "is_active", True),
            })
        return {"users": out}
    except Exception as e:
        raise RuntimeError(f"Lỗi khi lấy danh sách người dùng CVAT: {e}")


def assign_job(req, actor):
    """Lead phân công Job cho Annotator / Reviewer."""
    require(actor, "admin")
    job_id = int(req.get("job_id"))
    assignee_val = req.get("assignee")  # user id or None
    if assignee_val is not None and str(assignee_val).strip() != "":
        assignee_val = int(assignee_val)
    else:
        assignee_val = None

    client = _get_active_client()
    job = client.jobs.retrieve(job_id)
    updated = job.update({"assignee": assignee_val})
    new_assignee = updated.assignee.username if updated.assignee else None

    # Cập nhật metadata
    meta = _load_job_meta()
    job_meta = meta.get(str(job_id), {})
    if updated.stage == "annotation":
        job_meta["annotator_id"] = assignee_val
        job_meta["annotator_name"] = new_assignee
    elif updated.stage == "validation":
        job_meta["reviewer_id"] = assignee_val
        job_meta["reviewer_name"] = new_assignee
    meta[str(job_id)] = job_meta
    _save_job_meta(meta)

    return {
        "ok": True,
        "job_id": job_id,
        "assignee": new_assignee,
    }


def submit_for_validation(req, actor):
    """Annotator hoàn tất job và gửi sang stage 'validation' (Reviewer QC), gán assignee sang Reviewer."""
    job_id = int(req.get("job_id"))
    reviewer_id = req.get("reviewer_id")
    if reviewer_id is not None and str(reviewer_id).strip() != "":
        reviewer_id = int(reviewer_id)
    else:
        reviewer_id = None

    client = _get_active_client()
    job = client.jobs.retrieve(job_id)

    # Ghi nhận thông tin annotator vào metadata trước khi chuyển giao
    meta = _load_job_meta()
    current_assignee_id = job.assignee.id if getattr(job, "assignee", None) else None
    current_assignee_name = job.assignee.username if getattr(job, "assignee", None) else actor.get("username")
    annotator_id = current_assignee_id or actor.get("id")
    annotator_name = current_assignee_name or actor.get("username")

    reviewer_name = None
    if reviewer_id:
        try:
            rev_user = client.users.retrieve(reviewer_id)
            reviewer_name = rev_user.username
        except Exception:
            pass

    meta[str(job_id)] = {
        "annotator_id": annotator_id,
        "annotator_name": annotator_name,
        "reviewer_id": reviewer_id,
        "reviewer_name": reviewer_name,
        "submitted_at": time.time(),
    }
    _save_job_meta(meta)

    # Cập nhật stage='validation', state='completed' và gán assignee sang Reviewer (nếu có)
    update_data = {"stage": "validation", "state": "completed"}
    if reviewer_id:
        update_data["assignee"] = reviewer_id
    updated = job.update(update_data)

    new_assignee = updated.assignee.username if updated.assignee else None
    target_reviewer = reviewer_name or new_assignee or "Reviewer"
    return {
        "ok": True,
        "job_id": job_id,
        "stage": updated.stage,
        "state": updated.state,
        "assignee": new_assignee,
        "annotator": annotator_name,
        "reviewer": target_reviewer,
        "message": f"Job #{job_id} đã được gửi sang Validation cho {target_reviewer} tiến hành QC.",
    }


def open_cvat_job(req, actor):
    """Tải toàn bộ track từ CVAT job và chạy idcheck rules để Reviewer kiểm tra trên Studio."""
    job_id = int(req.get("job_id"))
    fps = float(req.get("fps") or 5.0)
    config_name = req.get("config")

    client = _get_active_client()
    job = client.jobs.retrieve(job_id)

    # Tải tracks có nội suy tuyến tính các frame giữa keyframe
    tracks, meta = load_job_tracks(client, job_id, interpolate=True)

    # Chuyển đổi sang format box rows của Studio
    rows = []
    labels_set = set()
    for tid, tr in tracks.items():
        labels_set.add(tr.label)
        for fid, b in tr.boxes.items():
            rows.append({
                "track_id": tid,
                "frame_id": fid,
                "x": round(b.x1, 1),
                "y": round(b.y1, 1),
                "w": round(max(0.0, b.x2 - b.x1), 1),
                "h": round(max(0.0, b.y2 - b.y1), 1),
                "class_name": b.label,
                "outside": b.outside,
                "occluded": b.occluded,
                "keyframe": b.keyframe,
            })

    # Lấy kích thước frame từ CVAT meta
    w, h = meta.get("frame_size") or (1280, 720)
    start_f = getattr(job, "start_frame", 0)
    stop_f = getattr(job, "stop_frame", 0)
    total_frames = getattr(job, "frame_count", 0) or (stop_f - start_f + 1 if stop_f >= start_f else 0)

    # Chạy kiểm tra bằng idcheck core rules
    scan_body = {
        "boxes": rows,
        "width": w,
        "height": h,
        "fps": fps,
        "mode": "video",
        "total_frames": total_frames,
        "frame_base": start_f,
        "config": config_name,
        "include_merges": True,
    }
    scan_res = scan(scan_body)

    host = getattr(client, "url", os.environ.get("CVAT_HOST", "https://cvat.trantuli.id.vn")).rstrip("/")
    assignee_name = job.assignee.username if getattr(job, "assignee", None) else None

    return {
        "job_id": job_id,
        "task_id": job.task_id,
        "stage": getattr(job, "stage", "annotation"),
        "state": getattr(job, "state", "new"),
        "assignee": assignee_name,
        "boxes": rows,
        "issues": scan_res["issues"],
        "counts": scan_res["counts"],
        "count": scan_res["count"],
        "stats": scan_res["stats"],
        "width": w,
        "height": h,
        "fps": fps,
        "total_frames": total_frames,
        "frame_base": start_f,
        "cvat_media_url_pattern": f"/api/cvat/frame/{job_id}/{{frame_id}}",
        "labels": sorted(labels_set) or ["Car"],
        "cvat_url": f"{host}/tasks/{job.task_id}/jobs/{job_id}",
    }


def reject_and_raise_issues(req, actor):
    """Reviewer từ chối Job, tạo issues trên CVAT tại đúng frame/box và chuyển stage về annotation."""
    require(actor, "reviewer", "admin")
    job_id = int(req.get("job_id"))
    client = _get_active_client()
    job = client.jobs.retrieve(job_id)

    # Lấy tracks hiện tại để xác định vị trí box cho issue
    tracks, _ = load_job_tracks(client, job_id, interpolate=True)

    input_issues = req.get("issues", [])
    alerts = []
    for iss in input_issues:
        # Bỏ qua những issue mà Reviewer đã đánh dấu false_positive hoặc đã bỏ chọn
        if iss.get("status") == "false_positive" or iss.get("rejected_by_reviewer"):
            continue

        raw_type = iss.get("type", "alert").lower().replace(" ", "_")
        alerts.append({
            "type": raw_type,
            "track_id": iss.get("track_id"),
            "frame": int(iss.get("frame", 0)),
            "detail": iss.get("message") or iss.get("detail", "Issue phát hiện bởi idcheck Studio"),
            "other_id": iss.get("data", {}).get("target"),
        })

    # Tạo CVAT issues trực tiếp qua API
    created_results = []
    if alerts:
        created_results = post_alerts_as_issues(client, job_id, alerts, tracks=tracks, skip_duplicates=True)

    # Tìm lại annotator gốc từ metadata để trả về đúng người
    meta = _load_job_meta()
    job_meta = meta.get(str(job_id), {})
    annotator_id = job_meta.get("annotator_id")
    annotator_name = job_meta.get("annotator_name")

    # Cập nhật Job sang stage='annotation', state='rejected'
    update_data = {"stage": "annotation", "state": "rejected"}
    if annotator_id:
        update_data["assignee"] = int(annotator_id)
    updated = job.update(update_data)

    if job_meta:
        job_meta["rejected_at"] = time.time()
        job_meta["rejected_by"] = actor.get("username")
        meta[str(job_id)] = job_meta
        _save_job_meta(meta)

    returned_to = annotator_name or (updated.assignee.username if updated.assignee else "Annotator")
    return {
        "ok": True,
        "job_id": job_id,
        "stage": updated.stage,
        "state": updated.state,
        "assignee": updated.assignee.username if updated.assignee else None,
        "created_issues_count": len([r for r in created_results if r.get("status") == "created"]),
        "total_alerts": len(alerts),
        "results": created_results,
        "message": f"Đã raise {len(alerts)} issues lên CVAT và trả lại Job #{job_id} cho Annotator ({returned_to}).",
    }


def accept_job(req, actor):
    """Reviewer chấp thuận hoàn thành Job -> stage 'acceptance', state 'completed'."""
    require(actor, "reviewer", "admin")
    job_id = int(req.get("job_id"))
    client = _get_active_client()
    job = client.jobs.retrieve(job_id)

    updated = job.update({"stage": "acceptance", "state": "completed"})
    return {
        "ok": True,
        "job_id": job_id,
        "stage": updated.stage,
        "state": updated.state,
        "message": f"Job #{job_id} đã được nghiệm thu (stage=acceptance, state=completed).",
    }


def get_job_frame(job_id, frame_id):
    """Lấy frame ảnh trực tiếp từ CVAT với disk cache để scrubbing siêu mượt."""
    cache_file = FRAME_CACHE_DIR / f"{job_id}_{frame_id}.jpg"
    if cache_file.is_file():
        return cache_file.read_bytes()

    client = _get_active_client()
    job = client.jobs.retrieve(int(job_id))

    try:
        frame_io = job.get_frame(int(frame_id), quality="compressed")
        if hasattr(frame_io, "getvalue"):
            data = frame_io.getvalue()
        elif hasattr(frame_io, "read"):
            data = frame_io.read()
        else:
            data = bytes(frame_io)

        # Lưu vào cache đĩa
        try:
            cache_file.write_bytes(data)
        except Exception:
            pass
        return data
    except Exception as e:
        logger.error("Error retrieving frame %s for job %s from CVAT: %s", frame_id, job_id, e)
        raise
