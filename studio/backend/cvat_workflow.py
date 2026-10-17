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
REPO_ROOT = ROOT.parent
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


def _get_active_client(org_slug=None):
    now = time.time()
    with LOCK:
        if _CLIENT_CACHE["client"] is not None and (now - _CLIENT_CACHE["timestamp"]) < CACHE_TTL:
            client = _CLIENT_CACHE["client"]
            if org_slug is not None:
                client.organization_slug = org_slug
            return client
        try:
            client = get_client()
            if org_slug is not None:
                client.organization_slug = org_slug
            _CLIENT_CACHE["client"] = client
            _CLIENT_CACHE["timestamp"] = now
            return client
        except Exception as e:
            logger.error("Failed to connect to CVAT: %s", e)
            raise ConnectionError(f"Không thể kết nối đến máy chủ CVAT: {e}")


def compute_job_role(job_dict, actor):
    """
    Xác định vai trò của người dùng đối với một Job cụ thể (Job-level Role):
    - 'admin': Quyền quản trị toàn hệ thống (Lead / Admin).
    - 'annotator': Người làm nhãn job này (khi stage='annotation', hoặc là tác giả nhãn).
    - 'reviewer': Người được giao review job này, hoặc đồng nghiệp review chéo khi job ở stage='validation'/'acceptance'.
    - 'viewer': Chỉ xem thông tin tổng quan.
    """
    actor_role = actor.get("role", "worker")
    if actor_role in ("admin", "owner"):
        return "admin"

    uname = (actor.get("username") or "").strip().lower()
    assignee = (job_dict.get("assignee") or "").strip().lower()
    annotator = (job_dict.get("annotator_name") or "").strip().lower()
    reviewer = (job_dict.get("reviewer_name") or "").strip().lower()
    stage = job_dict.get("stage", "annotation")

    is_annotator = (annotator == uname) or (assignee == uname and stage == "annotation")
    is_designated_reviewer = (reviewer == uname) or (assignee == uname and stage == "validation")

    if stage == "annotation":
        if is_annotator or (not assignee and not annotator):
            return "annotator"
        return "viewer"
    elif stage in ("validation", "acceptance"):
        if is_annotator:
            return "annotator"  # Người làm nhãn xem job của mình (không được tự accept/reject)
        elif actor_role in ("supervisor", "reviewer") or is_designated_reviewer or (not is_annotator):
            # Supervisor / Reviewer hoặc đồng nghiệp review chéo
            return "reviewer"

    return "viewer"


def list_jobs(actor, req=None):
    """Lấy danh sách CVAT jobs kèm thông tin project, task, assignee và lọc theo vai trò."""
    active_org = (actor.get("active_org") if actor else None)
    client = _get_active_client(active_org)
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
            "project_id": p_id,
            "project_name": p_name,
            "stage": getattr(j, "stage", "annotation"),
            "state": getattr(j, "state", "new"),
            "assignee": assignee_name,
            "assignee_id": assignee_id,
            "annotator_name": annotator_name,
            "annotator_id": job_meta.get("annotator_id"),
            "reviewer_name": reviewer_name,
            "reviewer_id": job_meta.get("reviewer_id"),
            "frame_count": frame_cnt,
            "start_frame": start_f,
            "stop_frame": stop_f,
            "cvat_url": f"{host}/tasks/{j.task_id}/jobs/{j.id}",
        })

    # Thu thập danh sách dự án (Projects) trên CVAT
    cvat_projects = []
    seen_pids = set()
    for j in all_jobs:
        pid = j.get("project_id")
        pname = j.get("project_name")
        if pid and pid not in seen_pids:
            seen_pids.add(pid)
            cvat_projects.append({"id": pid, "name": pname})
    for pid, pname in projects.items():
        if pid not in seen_pids:
            seen_pids.add(pid)
            cvat_projects.append({"id": pid, "name": pname})
    cvat_projects.sort(key=lambda p: str(p["name"]).lower())

    # Sắp xếp theo ID giảm dần (mới nhất lên trước)
    all_jobs.sort(key=lambda x: x["id"], reverse=True)

    role = actor.get("role", "annotator")
    user_name = actor.get("username", "")
    user_name_lower = user_name.strip().lower()

    # Tính toán vai trò từng Job (Job-level Role) cho người dùng hiện tại:
    for j in all_jobs:
        j["role_for_job"] = compute_job_role(j, actor)
        is_my_anno = bool(j["annotator_name"] and j["annotator_name"].strip().lower() == user_name_lower) or                      bool(j["assignee"] and j["assignee"].strip().lower() == user_name_lower and j["stage"] == "annotation")
        j["is_my_annotation"] = is_my_anno
        j["is_my_review"] = bool(j["stage"] in ("validation", "acceptance") and not is_my_anno and (
            (j["reviewer_name"] and j["reviewer_name"].strip().lower() == user_name_lower) or
            (j["assignee"] and j["assignee"].strip().lower() == user_name_lower) or
            True  # Có thể review chéo bất kỳ job validation nào của đồng nghiệp
        ))

    req = req or {}
    tab = req.get("tab")
    filtered_jobs = []

    if tab == "my_annotation":
        filtered_jobs = [j for j in all_jobs if j.get("is_my_annotation")]
    elif tab == "my_review":
        filtered_jobs = [j for j in all_jobs if j.get("is_my_review") and j["stage"] == "validation"]
    elif tab == "acceptance":
        filtered_jobs = [j for j in all_jobs if j["stage"] == "acceptance"]
    elif tab == "all":
        filtered_jobs = all_jobs
    else:
        # Mặc định tương thích ngược hoàn toàn
        if role == "annotator":
            for j in all_jobs:
                is_assigned_to_me = j["assignee"] and j["assignee"].lower() == user_name_lower
                is_unassigned = not j["assignee"]
                if (is_assigned_to_me or is_unassigned) and j["stage"] == "annotation":
                    filtered_jobs.append(j)
        elif role == "reviewer":
            for j in all_jobs:
                if j["stage"] in ("validation", "acceptance") or j.get("state") in ("rejected", "completed"):
                    filtered_jobs.append(j)
            if not filtered_jobs:
                filtered_jobs = all_jobs
        else:
            filtered_jobs = all_jobs

    return {
        "jobs": filtered_jobs,
        "all_jobs": all_jobs,
        "all_count": len(all_jobs),
        "filtered_count": len(filtered_jobs),
        "counts": {
            "all": len(all_jobs),
            "my_annotation": len([j for j in all_jobs if j.get("is_my_annotation")]),
            "my_review": len([j for j in all_jobs if j.get("is_my_review") and j["stage"] == "validation"]),
            "acceptance": len([j for j in all_jobs if j["stage"] == "acceptance"]),
        },
        "role": role,
        "username": user_name,
        "active_org": active_org,
        "cvat_host": host,
        "projects": cvat_projects,
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
    """Lead phân công Job cho cặp đôi Annotator & Reviewer."""
    require(actor, "admin")
    job_id = int(req.get("job_id"))
    client = _get_active_client()
    job = client.jobs.retrieve(job_id)

    meta = _load_job_meta()
    job_meta = meta.get(str(job_id), {})

    annotator_id = req.get("annotator_id")
    reviewer_id = req.get("reviewer_id")
    # Tương thích ngược nếu client cũ chỉ truyền 'assignee':
    if "assignee" in req and annotator_id is None and reviewer_id is None:
        assignee_val = req.get("assignee")
        if job.stage == "annotation":
            annotator_id = assignee_val
        else:
            reviewer_id = assignee_val

    try:
        users_by_id = {u.id: u.username for u in client.users.list()}
    except Exception:
        users_by_id = {}

    if annotator_id is not None and str(annotator_id).strip() != "":
        annotator_id = int(annotator_id)
        job_meta["annotator_id"] = annotator_id
        job_meta["annotator_name"] = users_by_id.get(annotator_id) or f"User #{annotator_id}"
    elif "annotator_id" in req:
        job_meta["annotator_id"] = None
        job_meta["annotator_name"] = None

    if reviewer_id is not None and str(reviewer_id).strip() != "":
        reviewer_id = int(reviewer_id)
        job_meta["reviewer_id"] = reviewer_id
        job_meta["reviewer_name"] = users_by_id.get(reviewer_id) or f"User #{reviewer_id}"
    elif "reviewer_id" in req:
        job_meta["reviewer_id"] = None
        job_meta["reviewer_name"] = None

    # Xác định người gán trực tiếp trên CVAT hiện tại theo stage:
    if job.stage == "annotation":
        cvat_assignee_id = job_meta.get("annotator_id")
    else:
        cvat_assignee_id = job_meta.get("reviewer_id")

    updated = job.update({"assignee": cvat_assignee_id})
    meta[str(job_id)] = job_meta
    _save_job_meta(meta)

    return {
        "ok": True,
        "job_id": job_id,
        "assignee": updated.assignee.username if updated.assignee else None,
        "annotator_name": job_meta.get("annotator_name"),
        "reviewer_name": job_meta.get("reviewer_name"),
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
    job_info = meta.get(str(job_id), {})

    current_assignee_id = job.assignee.id if getattr(job, "assignee", None) else None
    current_assignee_name = job.assignee.username if getattr(job, "assignee", None) else None

    # Ưu tiên annotator_id đã lưu trong meta hoặc assignee hiện tại trên CVAT
    annotator_id = job_info.get("annotator_id") or current_assignee_id or actor.get("id")
    annotator_name = job_info.get("annotator_name") or current_assignee_name or actor.get("username")

    # Nếu không truyền reviewer_id, dùng reviewer_id từ meta hoặc gán cho chính actor nếu actor là reviewer/supervisor
    if not reviewer_id and job_info.get("reviewer_id"):
        reviewer_id = job_info.get("reviewer_id")
    if not reviewer_id and actor.get("id"):
        if actor.get("id") != annotator_id or actor.get("role") in ("reviewer", "supervisor", "admin"):
            reviewer_id = actor.get("id")

    is_admin = actor.get("role") == "admin"
    is_annotator = bool(actor.get("id") and annotator_id and actor.get("id") == annotator_id)
    if reviewer_id and actor.get("id") and reviewer_id == actor.get("id") and is_annotator and not is_admin:
        raise ValueError("Vui lòng chỉ định một đồng nghiệp khác làm Reviewer để đảm bảo tính khách quan (Annotator không tự review nhãn chính mình).")

    reviewer_name = None
    if reviewer_id:
        try:
            rev_user = client.users.retrieve(reviewer_id)
            reviewer_name = rev_user.username
        except Exception:
            pass

    meta[str(job_id)] = {
        **job_info,
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
            if getattr(b, "outside", False):
                continue
            rows.append({
                "uid": f"cvat_{tid}_{fid}",
                "track_id": tid,
                "frame_id": fid,
                "x": round(b.x1, 1),
                "y": round(b.y1, 1),
                "w": round(max(0.0, b.x2 - b.x1), 1),
                "h": round(max(0.0, b.y2 - b.y1), 1),
                "class_name": b.label,
                "outside": False,
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

    # Lấy thông tin annotator và reviewer từ metadata
    meta_store = _load_job_meta()
    job_meta = meta_store.get(str(job_id), {})
    annotator_name = job_meta.get("annotator_name")
    if not annotator_name and getattr(job, "stage", "") == "annotation" and assignee_name:
        annotator_name = assignee_name
    reviewer_name = job_meta.get("reviewer_name")
    if not reviewer_name and getattr(job, "stage", "") == "validation" and assignee_name:
        reviewer_name = assignee_name

    job_dict = {
        "stage": getattr(job, "stage", "annotation"),
        "state": getattr(job, "state", "new"),
        "assignee": assignee_name,
        "annotator_name": annotator_name,
        "reviewer_name": reviewer_name,
    }
    effective_role = compute_job_role(job_dict, actor)
    user_name_lower = (actor.get("username") or "").strip().lower()
    is_own_job = bool(annotator_name and annotator_name.strip().lower() == user_name_lower)
    is_admin = actor.get("role") == "admin"
    can_review = bool((effective_role in ("reviewer", "admin")) and not (is_own_job and not is_admin))
    can_edit_boxes = bool((effective_role in ("annotator", "admin")) and getattr(job, "stage", "") == "annotation")

    return {
        "job_id": job_id,
        "task_id": job.task_id,
        "stage": getattr(job, "stage", "annotation"),
        "state": getattr(job, "state", "new"),
        "assignee": assignee_name,
        "annotator_name": annotator_name,
        "reviewer_name": reviewer_name,
        "effective_role": effective_role,
        "can_review": can_review,
        "can_edit_boxes": can_edit_boxes,
        "is_own_job": is_own_job,
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
        "config": config_name or "config_project_3.yaml",
    }


def reject_and_raise_issues(req, actor):
    """Reviewer từ chối Job, tạo issues trên CVAT tại đúng frame/box và chuyển stage về annotation."""
    job_id = int(req.get("job_id"))
    client = _get_active_client()
    job = client.jobs.retrieve(job_id)

    meta = _load_job_meta()
    job_meta = meta.get(str(job_id), {})
    annotator_name = job_meta.get("annotator_name")
    user_name = actor.get("username", "")
    is_admin = actor.get("role") == "admin"

    # Chống tự từ chối job của chính mình:
    if not is_admin and annotator_name and annotator_name.strip().lower() == user_name.strip().lower():
        raise PermissionError("Không thể tự từ chối (Reject) job do chính mình làm nhãn. Hãy để đồng nghiệp review chéo.")

    job_dict = {
        "stage": getattr(job, "stage", "annotation"),
        "assignee": job.assignee.username if getattr(job, "assignee", None) else None,
        "annotator_name": annotator_name,
        "reviewer_name": job_meta.get("reviewer_name"),
    }
    role_for_job = compute_job_role(job_dict, actor)
    if role_for_job not in ("reviewer", "admin") and not is_admin:
        raise PermissionError("Bạn không có quyền Reviewer đối với Job này.")

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
    job_id = int(req.get("job_id"))
    client = _get_active_client()
    job = client.jobs.retrieve(job_id)

    meta = _load_job_meta()
    job_meta = meta.get(str(job_id), {})
    annotator_name = job_meta.get("annotator_name")
    user_name = actor.get("username", "")
    is_admin = actor.get("role") == "admin"

    # Chống tự duyệt (Anti self-approval):
    if not is_admin and annotator_name and annotator_name.strip().lower() == user_name.strip().lower():
        raise PermissionError("Không thể tự nghiệm thu (Accept) job do chính mình làm nhãn. Hãy để đồng nghiệp review chéo.")

    job_dict = {
        "stage": getattr(job, "stage", "annotation"),
        "assignee": job.assignee.username if getattr(job, "assignee", None) else None,
        "annotator_name": annotator_name,
        "reviewer_name": job_meta.get("reviewer_name"),
    }
    role_for_job = compute_job_role(job_dict, actor)
    if role_for_job not in ("reviewer", "admin") and not is_admin:
        raise PermissionError("Bạn không có quyền Reviewer đối với Job này.")

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


def _lerp(a, b, t):
    return [av + (bv - av) * t for av, bv in zip(a, b)]


def _keep_keyframes(points, tolerance=0.5):
    """Indexes of boxes that must stay keyframes so CVAT's linear interpolation reproduces every box
    within `tolerance` px (Ramer–Douglas–Peucker over time)."""
    if len(points) <= 2:
        return list(range(len(points)))
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        (fi, bi), (fj, bj) = points[i], points[j]
        worst, worst_k = 0.0, -1
        for k in range(i + 1, j):
            fk, bk = points[k]
            expect = _lerp(bi, bj, (fk - fi) / (fj - fi))
            err = max(abs(e - a) for e, a in zip(expect, bk))
            if err > worst:
                worst, worst_k = err, k
        if worst > tolerance:
            keep.add(worst_k)
            stack += [(i, worst_k), (worst_k, j)]
    return sorted(keep)


def boxes_to_cvat(boxes, label_ids, stop_frame):
    """Convert studio boxes -> CVAT annotations payload with sparse keyframes."""
    missing = sorted({b['class_name'] for b in boxes} - set(label_ids))
    if missing:
        raise ValueError('Job CVAT không có nhãn: ' + ', '.join(missing) + '. Vui lòng thêm nhãn trên CVAT hoặc đổi class.')

    def shape(b, outside=False, keyframe=True):
        return {
            'type': 'rectangle',
            'frame': b['frame_id'],
            'points': [b['x'], b['y'], b['x'] + b['w'], b['y'] + b['h']],
            'occluded': bool(b.get('occluded')),
            'outside': outside,
            'keyframe': keyframe,
            'z_order': 0,
            'rotation': 0,
            'attributes': []
        }

    tracks, shapes = [], []
    by_track = {}
    for b in boxes:
        if b.get('track_id', -1) < 0:
            shapes.append({**shape(b), 'label_id': label_ids[b['class_name']], 'source': 'manual', 'group': 0})
        else:
            by_track.setdefault(b['track_id'], []).append(b)

    for tid, group in sorted(by_track.items()):
        group.sort(key=lambda b: b['frame_id'])
        classes = {b['class_name'] for b in group}
        if len(classes) > 1:
            raise ValueError(f'Track #{tid} có nhiều class ({sorted(classes)}). Hãy đồng nhất class trước khi đẩy lên CVAT.')
        runs, cur = [], [group[0]]
        for b in group[1:]:
            if b['frame_id'] == cur[-1]['frame_id'] + 1:
                cur.append(b)
            else:
                runs.append(cur)
                cur = [b]
        runs.append(cur)

        out = []
        for run in runs:
            pts = [(b['frame_id'], [b['x'], b['y'], b['x'] + b['w'], b['y'] + b['h']]) for b in run]
            for k in _keep_keyframes(pts):
                out.append(shape(run[k]))
            if run[-1]['frame_id'] < stop_frame:
                out.append(shape({**run[-1], 'frame_id': run[-1]['frame_id'] + 1}, outside=True))

        tracks.append({
            'frame': group[0]['frame_id'],
            'label_id': label_ids[group[0]['class_name']],
            'group': 0,
            'source': 'manual',
            'attributes': [],
            'shapes': out
        })

    return {'version': 0, 'tags': [], 'shapes': shapes, 'tracks': tracks}


def push_job_annotations(req, actor):
    """Đẩy annotations sạch từ Studio về lại CVAT với sparse keyframes và sao lưu dữ liệu gốc."""
    require(actor, "annotator", "reviewer", "admin")
    job_id = int(req.get("job_id"))
    boxes = req.get("boxes")
    if boxes is None:
        raise ValueError("Không có danh sách bounding boxes để đẩy về CVAT")
    client = _get_active_client()
    job = client.jobs.retrieve(job_id)
    labels = {lbl.name: lbl.id for lbl in job.get_labels()}
    
    stop_frame = int(req.get("stop_frame") or job.stop_frame)
    payload = boxes_to_cvat(boxes, labels, stop_frame)
    
    # Sao lưu annotations hiện tại trước khi ghi đè
    backup_dir = ROOT / "storage" / "cvat_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y%m%d-%H%M%S')
    backup_path = backup_dir / f"job_{job_id}_{ts}.json"
    
    try:
        resp = client.api_client.call_api(
            f'/api/jobs/{job_id}/annotations', 'GET',
            header_params={'Accept': 'application/vnd.cvat+json'},
            _parse_response=False
        )
        if resp and resp[1].data:
            backup_path.write_bytes(resp[1].data)
    except Exception as exc:
        logger.warning("Không thể tạo backup CVAT trước khi push: %s", exc)
        
    # Ghi đè bằng call_api PUT
    client.api_client.call_api(
        f'/api/jobs/{job_id}/annotations', 'PUT',
        header_params={'Content-Type': 'application/json', 'Accept': 'application/vnd.cvat+json'},
        body=payload,
        _parse_response=False
    )
    
    return {
        "ok": True,
        "job_id": job_id,
        "tracks": len(payload['tracks']),
        "shapes": len(payload['shapes']),
        "keyframes": sum(len(t['shapes']) for t in payload['tracks']),
        "backup_file": backup_path.name,
        "message": f"Đã đẩy thành công {len(payload['tracks'])} tracks (được nén keyframe) về Job #{job_id}. Bản gốc đã sao lưu tại {backup_path.name}."
    }



def list_organizations(actor):
    """Lấy danh sách các tổ chức (Organizations) mà người dùng có quyền truy cập trên CVAT."""
    client = _get_active_client()
    uname = actor.get("username", "")
    is_admin = actor.get("role") in ("admin", "owner") or actor.get("is_staff", False)

    from cvat_sdk.api_client.api import organizations_api, memberships_api
    all_orgs = []
    try:
        o_api = organizations_api.OrganizationsApi(client.api_client)
        o_res, _ = o_api.list()
        all_orgs = o_res.results or []
    except Exception as e:
        logger.warning("Could not list organizations: %s", e)

    m_dict = {}
    try:
        m_api = memberships_api.MembershipsApi(client.api_client)
        m_res, _ = m_api.list(user=uname)
        for m in (m_res.results or []):
            m_dict[m.organization] = m.role
    except Exception as e:
        logger.warning("Could not list user memberships: %s", e)

    orgs_out = []
    for o in all_orgs:
        if is_admin or o.id in m_dict:
            orgs_out.append({
                "id": o.id,
                "slug": o.slug,
                "name": o.name or o.slug,
                "role": m_dict.get(o.id, "owner" if is_admin else "worker"),
            })

    # Tùy chọn Cá nhân (Personal Workspace)
    orgs_out.append({
        "id": 0,
        "slug": "",
        "name": "Cá nhân (Personal Workspace)",
        "role": "owner" if is_admin else "worker",
    })

    active_org = actor.get("active_org")
    if active_org is None:
        active_org = orgs_out[0]["slug"] if orgs_out else ""

    return {
        "organizations": orgs_out,
        "active_org": active_org,
    }


def set_active_organization(req, actor):
    """Chuyển đổi Organization đang làm việc và cập nhật vai trò của người dùng trong Organization đó."""
    from studio.backend import auth
    org_slug = (req.get("org_slug") or "").strip()
    uname = actor.get("username", "")
    token = actor.get("token")

    client = _get_active_client(org_slug)

    new_role = "worker"
    if actor.get("is_staff") or actor.get("role") == "admin":
        new_role = "admin"
    elif org_slug:
        try:
            from cvat_sdk.api_client.api import memberships_api, organizations_api
            m_api = memberships_api.MembershipsApi(client.api_client)
            m_res, _ = m_api.list(user=uname)
            o_api = organizations_api.OrganizationsApi(client.api_client)
            for m in (m_res.results or []):
                try:
                    o_obj, _ = o_api.retrieve(m.organization)
                    if o_obj.slug == org_slug:
                        if m.role in ("owner", "maintainer"):
                            new_role = "admin"
                        elif m.role == "supervisor":
                            new_role = "supervisor"
                        else:
                            new_role = "worker"
                        break
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Error checking role in org %s: %s", org_slug, e)
    else:
        new_role = "admin" if actor.get("is_staff") else "worker"

    if token:
        auth.update_session_org_and_role(token, org_slug, new_role)

    return {
        "ok": True,
        "active_org": org_slug,
        "role": new_role,
        "message": f"Đã chuyển sang tổ chức: {org_slug or 'Cá nhân (Personal)'} (Vai trò: {new_role.upper()})"
    }


def list_configs():
    """Liệt kê danh sách các file cấu hình YAML có sẵn trong repository."""
    configs = [p.name for p in sorted(REPO_ROOT.glob("config_*.yaml"))]
    if "config_base.yaml" not in configs:
        configs.insert(0, "config_base.yaml")
    return {"configs": configs}


def calibrate_from_jobs(req, actor):
    """
    Tự động hiệu chỉnh ngưỡng (Auto-Calibrate) từ các Job đã nghiệm thu (stage='acceptance').
    Quy trình:
    1. Tải tracks sạch từ các Job mẫu trên CVAT.
    2. Chèn lỗi nhân tạo (jump, swap, gap, merge) để tạo tập test.
    3. Grid-search F1-score để tìm ngưỡng tối ưu (hạn chế tối đa báo oan).
    4. Lưu file cấu hình config_project_<id>.yaml và trả về thông số tối ưu.
    """
    require(actor, "supervisor", "reviewer", "admin")
    from tools.tune.calibrate_project import (
        tune_gap_params, tune_max_speed, tune_switch_params, tune_merge_params
    )
    from adapters.cvat_api import load_job_tracks
    from core.inject import inject_many
    import copy
    import yaml

    client = _get_active_client(actor.get("active_org"))
    job_ids = req.get("job_ids") or []
    if not job_ids:
        # Nếu không chỉ định, lấy toàn bộ jobs ở stage acceptance
        jobs_res = list_jobs(actor)
        job_ids = [j["id"] for j in jobs_res.get("all_jobs", []) if j.get("stage") == "acceptance"]

    if not job_ids:
        raise ValueError("Không tìm thấy Job nào ở trạng thái 'acceptance' (Đã nghiệm thu) để làm mẫu Ground Truth.")

    project_id = req.get("project_id")
    project_name = req.get("project_name") or f"Project #{project_id or 'Auto'}"
    fps = float(req.get("fps") or 5.0)

    out_name = (req.get("out_name") or f"config_project_{project_id or 'tuned'}.yaml").strip()
    if not out_name.endswith(".yaml") and not out_name.endswith(".yml"):
        out_name += ".yaml"

    base_config_path = req.get("base_config")
    if not base_config_path:
        if project_id and (REPO_ROOT / f"config_project_{project_id}.yaml").is_file():
            base_file = REPO_ROOT / f"config_project_{project_id}.yaml"
        elif (REPO_ROOT / "config_project_3.yaml").is_file():
            base_file = REPO_ROOT / "config_project_3.yaml"
        else:
            base_file = REPO_ROOT / "config_base.yaml"
    else:
        base_file = REPO_ROOT / base_config_path
        if not base_file.is_file():
            base_file = REPO_ROOT / "config_project_3.yaml" if (REPO_ROOT / "config_project_3.yaml").is_file() else REPO_ROOT / "config_base.yaml"

    # 1. Tải tracks từ CVAT
    clean_data = {}
    for jid in job_ids:
        tracks, _ = load_job_tracks(client, jid, interpolate=True)
        if tracks:
            clean_data[jid] = tracks

    if not clean_data:
        raise ValueError("Không tải được track nào từ danh sách Job đã chọn.")

    total_tracks = sum(len(tr) for tr in clean_data.values())

    # 2. Sinh tập dữ liệu lỗi nhân tạo tổng hợp
    dirty_data = {}
    k = fps / 30.0
    for jid, clean in clean_data.items():
        dirty, truths = inject_many(
            clean,
            n_per_type=8,
            seed=jid,
            drop_range=(max(2, round(2 * k)), max(4, round(8 * k))),
            gap_range=(max(2, round(2 * k)), max(4, round(6 * k))),
            jump_range=(1.5, 4.0),
        )
        dirty_data[jid] = (dirty, truths)

    # 3. Đọc cấu hình nền
    with open(base_file, "r", encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f) or {}

    # 4. Chạy tối ưu hóa ngưỡng (Grid search F1-score toàn diện)
    opt_min_gap, opt_max_gap, opt_skip_occ = tune_gap_params(clean_data, dirty_data, base_cfg, fps)
    opt_speed, opt_step = tune_max_speed(clean_data, dirty_data, base_cfg, fps)
    opt_sep, opt_ratio = tune_switch_params(clean_data, dirty_data, base_cfg, fps)
    opt_cost, opt_gap_s, opt_gap_cost = tune_merge_params(clean_data, dirty_data, base_cfg, fps)

    # 5. Lưu file cấu hình mới
    tuned_cfg = copy.deepcopy(base_cfg)
    tuned_cfg.setdefault("default", {})
    tuned_cfg["default"]["min_missing_gap_s"] = opt_min_gap
    tuned_cfg["default"]["max_missing_gap_s"] = opt_max_gap
    tuned_cfg["default"]["skip_occluded_gap"] = opt_skip_occ
    tuned_cfg["default"]["max_speed"] = opt_speed
    tuned_cfg["default"]["max_frame_step_s"] = opt_step
    tuned_cfg["default"]["swap_min_sep"] = opt_sep
    tuned_cfg["default"]["swap_cross_ratio"] = opt_ratio
    tuned_cfg["default"]["merge_max_cost"] = opt_cost
    tuned_cfg["default"]["max_merge_gap_s"] = opt_gap_s
    tuned_cfg["default"]["merge_gap_cost_s"] = opt_gap_cost

    for cls_name in ("car", "truck", "other vehicle"):
        tuned_cfg.setdefault("classes", {}).setdefault(cls_name, {})
        tuned_cfg["classes"][cls_name]["max_speed"] = opt_speed

    out_file = REPO_ROOT / out_name
    with open(out_file, "w", encoding="utf-8") as f:
        yaml.dump(tuned_cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return {
        "ok": True,
        "config_file": out_name,
        "parameters": {
            "min_missing_gap_s": opt_min_gap,
            "max_missing_gap_s": opt_max_gap,
            "skip_occluded_gap": opt_skip_occ,
            "max_speed": opt_speed,
            "max_frame_step_s": opt_step,
            "swap_min_sep": opt_sep,
            "swap_cross_ratio": opt_ratio,
            "merge_max_cost": opt_cost,
            "max_merge_gap_s": opt_gap_s,
            "merge_gap_cost_s": opt_gap_cost,
        },
        "jobs_used": job_ids,
        "total_tracks": total_tracks,
        "fps": fps,
        "project_name": project_name,
        "message": f"Đã tối ưu hóa ngưỡng và lưu cấu hình '{out_name}' thành công từ {len(job_ids)} Job mẫu ({total_tracks} tracks)!"
    }

