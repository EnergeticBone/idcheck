from unittest.mock import MagicMock, patch
import pytest

from studio.backend import cvat_workflow
from core.schema import Box, Track


class DummyUser:
    def __init__(self, id, username, is_staff=False):
        self.id = id
        self.username = username
        self.is_staff = is_staff


class DummyJob:
    def __init__(self, id, task_id, stage, state, assignee=None, start_frame=0, stop_frame=99):
        self.id = id
        self.task_id = task_id
        self.stage = stage
        self.state = state
        self.assignee = assignee
        self.start_frame = start_frame
        self.stop_frame = stop_frame
        self.frame_count = stop_frame - start_frame + 1

    def update(self, fields):
        if "stage" in fields:
            self.stage = fields["stage"]
        if "state" in fields:
            self.state = fields["state"]
        if "assignee" in fields:
            self.assignee = DummyUser(fields["assignee"], "assigned_user") if fields["assignee"] else None
        return self


def test_list_jobs_filtering():
    mock_client = MagicMock()
    mock_client.url = "https://cvat.test.vn"
    mock_client.projects.list.return_value = []
    mock_client.tasks.list.return_value = []

    user_annotator = DummyUser(10, "annotator_1")
    job_anno = DummyJob(1, 100, stage="annotation", state="new", assignee=user_annotator)
    job_val = DummyJob(2, 101, stage="validation", state="completed", assignee=user_annotator)
    job_other = DummyJob(3, 102, stage="annotation", state="new", assignee=DummyUser(11, "annotator_2"))

    mock_client.jobs.list.return_value = [job_anno, job_val, job_other]

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client):
        # Annotator view
        res_anno = cvat_workflow.list_jobs({"username": "annotator_1", "role": "annotator"})
        assert len(res_anno["jobs"]) == 1
        assert res_anno["jobs"][0]["id"] == 1

        # Reviewer view
        res_rev = cvat_workflow.list_jobs({"username": "reviewer_1", "role": "reviewer"})
        assert len(res_rev["jobs"]) == 1
        assert res_rev["jobs"][0]["id"] == 2
        assert res_rev["jobs"][0]["stage"] == "validation"

        # Admin view
        res_admin = cvat_workflow.list_jobs({"username": "admin", "role": "admin"})
        assert len(res_admin["jobs"]) == 3


def test_submit_for_validation():
    mock_client = MagicMock()
    job = DummyJob(1, 100, stage="annotation", state="new")
    mock_client.jobs.retrieve.return_value = job

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client):
        res = cvat_workflow.submit_for_validation({"job_id": 1}, {"username": "annotator_1", "role": "annotator"})
        assert res["ok"] is True
        assert res["stage"] == "validation"
        assert res["state"] == "completed"
        assert job.stage == "validation"
        assert job.state == "completed"


def test_accept_job():
    mock_client = MagicMock()
    job = DummyJob(1, 100, stage="validation", state="completed")
    mock_client.jobs.retrieve.return_value = job

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client):
        res = cvat_workflow.accept_job({"job_id": 1}, {"username": "reviewer_1", "role": "reviewer"})
        assert res["ok"] is True
        assert res["stage"] == "acceptance"
        assert res["state"] == "completed"
        assert job.stage == "acceptance"


def test_reject_and_raise_issues():
    mock_client = MagicMock()
    job = DummyJob(1, 100, stage="validation", state="completed")
    mock_client.jobs.retrieve.return_value = job

    issues = [
        {"type": "ID Switch Candidate", "frame": 15, "track_id": 2, "message": "ID switch between 2 and 5"},
        {"type": "Box Jump", "frame": 20, "track_id": 3, "message": "Sudden jump", "status": "false_positive"},
    ]

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client), \
         patch.object(cvat_workflow, "load_job_tracks", return_value=({}, {})), \
         patch.object(cvat_workflow, "post_alerts_as_issues", return_value=[{"status": "created"}]) as mock_post:
        res = cvat_workflow.reject_and_raise_issues(
            {"job_id": 1, "issues": issues},
            {"username": "reviewer_1", "role": "reviewer"}
        )
        assert res["ok"] is True
        assert res["stage"] == "annotation"
        assert res["state"] == "rejected"
        assert job.stage == "annotation"
        assert job.state == "rejected"
        # The false_positive issue should be filtered out
        assert mock_post.called
        alerts_arg = mock_post.call_args[0][2]
        assert len(alerts_arg) == 1
        assert alerts_arg[0]["track_id"] == 2


def test_assignment_handoff_and_restore(tmp_path):
    mock_client = MagicMock()
    user_annotator = DummyUser(10, "Tran_Dinh_Cuong")
    user_reviewer = DummyUser(20, "Nguyen_Dang_Huan")

    job = DummyJob(99, 50, stage="annotation", state="new", assignee=user_annotator)
    mock_client.jobs.retrieve.return_value = job
    mock_client.users.retrieve.side_effect = lambda uid: user_reviewer if uid == 20 else user_annotator

    fake_meta_file = tmp_path / "cvat_job_meta.json"

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client), \
         patch.object(cvat_workflow, "META_FILE", fake_meta_file):
        # 1. Annotator submits for validation, targeting reviewer 20
        res_submit = cvat_workflow.submit_for_validation(
            {"job_id": 99, "reviewer_id": 20},
            {"id": 10, "username": "Tran_Dinh_Cuong", "role": "annotator"}
        )
        assert res_submit["ok"] is True
        assert res_submit["stage"] == "validation"
        assert res_submit["state"] == "completed"
        assert job.assignee.id == 20  # Reassigned to Reviewer on CVAT!
        assert res_submit["reviewer"] == "Nguyen_Dang_Huan"

        # 2. Reviewer inspects and rejects
        with patch.object(cvat_workflow, "load_job_tracks", return_value=({}, {})), \
             patch.object(cvat_workflow, "post_alerts_as_issues", return_value=[]):
            res_reject = cvat_workflow.reject_and_raise_issues(
                {"job_id": 99, "issues": []},
                {"id": 20, "username": "Nguyen_Dang_Huan", "role": "reviewer"}
            )
            assert res_reject["ok"] is True
            assert res_reject["stage"] == "annotation"
            assert res_reject["state"] == "rejected"
            assert job.assignee.id == 10  # Automatically restored back to Annotator!


def test_job_level_role_peer_review(tmp_path):
    mock_client = MagicMock()
    user_cuong = DummyUser(10, "Tran_Dinh_Cuong")
    user_huan = DummyUser(20, "Nguyen_Dang_Huan")

    # Job 1 annotated by Cuong, submitted to validation
    job1 = DummyJob(1, 10, stage="validation", state="completed", assignee=user_huan)
    # Job 2 annotated by Huan, submitted to validation
    job2 = DummyJob(2, 20, stage="validation", state="completed", assignee=user_cuong)

    mock_client.jobs.retrieve.side_effect = lambda jid: job1 if jid == 1 else job2
    mock_client.jobs.list.return_value = [job1, job2]
    mock_client.projects.list.return_value = []
    mock_client.tasks.list.return_value = []

    fake_meta_file = tmp_path / "cvat_job_meta.json"
    fake_meta = {
        "1": {"annotator_id": 10, "annotator_name": "Tran_Dinh_Cuong", "reviewer_id": 20, "reviewer_name": "Nguyen_Dang_Huan"},
        "2": {"annotator_id": 20, "annotator_name": "Nguyen_Dang_Huan", "reviewer_id": 10, "reviewer_name": "Tran_Dinh_Cuong"},
    }
    import json
    fake_meta_file.write_text(json.dumps(fake_meta), encoding="utf-8")

    actor_cuong = {"id": 10, "username": "Tran_Dinh_Cuong", "role": "annotator"}

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client), \
         patch.object(cvat_workflow, "META_FILE", fake_meta_file):

        # 1. Cuong list jobs by tabs
        res_review = cvat_workflow.list_jobs(actor_cuong, {"tab": "my_review"})
        # Cuong can review Job 2 (Huan's job)
        assert len(res_review["jobs"]) == 1
        assert res_review["jobs"][0]["id"] == 2
        assert res_review["jobs"][0]["role_for_job"] == "reviewer"

        res_anno = cvat_workflow.list_jobs(actor_cuong, {"tab": "my_annotation"})
        # Cuong's own job is Job 1
        assert len(res_anno["jobs"]) == 1
        assert res_anno["jobs"][0]["id"] == 1
        assert res_anno["jobs"][0]["role_for_job"] == "annotator"

        # 2. Anti self-approval: Cuong CANNOT accept his own Job 1
        with pytest.raises(PermissionError, match="Không thể tự nghiệm thu"):
            cvat_workflow.accept_job({"job_id": 1}, actor_cuong)

        # 3. Peer review: Cuong CAN accept Job 2 (Huan's job)
        res_accept_2 = cvat_workflow.accept_job({"job_id": 2}, actor_cuong)
        assert res_accept_2["ok"] is True
        assert res_accept_2["stage"] == "acceptance"

        # 4. Anti self-reject: Cuong CANNOT reject his own Job 1
        with pytest.raises(PermissionError, match="Không thể tự từ chối"):
            cvat_workflow.reject_and_raise_issues({"job_id": 1, "issues": []}, actor_cuong)


def test_organizations_and_tiering():
    from studio.backend import auth, cvat_workflow

    # 1. Test auth.require hierarchy
    admin = {"username": "admin_user", "role": "admin"}
    supervisor = {"username": "qa_lead", "role": "supervisor"}
    reviewer = {"username": "peer_reviewer", "role": "reviewer"}
    worker = {"username": "annotator_user", "role": "worker"}
    annotator = {"username": "annotator_legacy", "role": "annotator"}

    # Admin satisfies all
    auth.require(admin, "admin")
    auth.require(admin, "reviewer")
    auth.require(admin, "supervisor")
    auth.require(admin, "worker")
    auth.require(admin, "annotator")

    # Supervisor satisfies reviewer / supervisor / worker / annotator
    auth.require(supervisor, "reviewer")
    auth.require(supervisor, "supervisor")
    auth.require(supervisor, "worker")
    auth.require(supervisor, "annotator")
    with pytest.raises(PermissionError):
        auth.require(supervisor, "admin")

    # Reviewer satisfies reviewer / supervisor
    auth.require(reviewer, "reviewer")
    with pytest.raises(PermissionError):
        auth.require(reviewer, "admin")

    # Worker satisfies worker / annotator
    auth.require(worker, "worker")
    auth.require(worker, "annotator")
    with pytest.raises(PermissionError):
        auth.require(worker, "reviewer")
    with pytest.raises(PermissionError):
        auth.require(worker, "admin")

    # 2. Test list_organizations
    mock_client = MagicMock()
    mock_org = MagicMock()
    mock_org.id = 1
    mock_org.slug = "IDCheck"
    mock_org.name = "IDCheck Organization"

    mock_o_api = MagicMock()
    mock_o_api.list.return_value = (MagicMock(results=[mock_org]), None)
    mock_m_api = MagicMock()
    mock_m = MagicMock()
    mock_m.organization = 1
    mock_m.role = "supervisor"
    mock_m_api.list.return_value = (MagicMock(results=[mock_m]), None)

    with patch("cvat_sdk.api_client.api.organizations_api.OrganizationsApi", return_value=mock_o_api), \
         patch("cvat_sdk.api_client.api.memberships_api.MembershipsApi", return_value=mock_m_api), \
         patch.object(cvat_workflow, "_get_active_client", return_value=mock_client):

        res = cvat_workflow.list_organizations({"username": "qa_lead", "role": "supervisor"})
        assert "organizations" in res
        orgs = res["organizations"]
        assert any(o["slug"] == "IDCheck" for o in orgs)
        assert any(o["slug"] == "" and "Cá nhân" in o["name"] for o in orgs)

        # 3. Test set_active_organization
        set_res = cvat_workflow.set_active_organization({"org_slug": "IDCheck"}, {"username": "qa_lead", "role": "supervisor"})
        assert set_res["ok"] is True
        assert set_res["active_org"] == "IDCheck"


def test_auto_tune_and_configs(tmp_path):
    from studio.backend import cvat_workflow
    from core.schema import Track, Box

    # 1. Test list_configs
    configs = cvat_workflow.list_configs()
    assert "configs" in configs
    assert any("config_base.yaml" in c for c in configs["configs"])

    # 2. Test calibrate_from_jobs permission check (admin only)
    worker = {"username": "annotator_cuong", "role": "worker"}
    reviewer = {"username": "qa_lead", "role": "reviewer"}
    admin = {"username": "admin_owner", "role": "admin"}

    with pytest.raises(PermissionError):
        cvat_workflow.calibrate_from_jobs({"job_ids": [7]}, worker)

    with pytest.raises(PermissionError):
        cvat_workflow.calibrate_from_jobs({"job_ids": [7]}, reviewer)

    # 3. Test calibrate_from_jobs execution with synthetic ground truth
    dummy_tracks = [
        Track(1, "car", {
            i: Box(i, 100.0 + i*2, 100.0, 150.0 + i*2, 150.0, "car")
            for i in range(15)
        }),
        Track(2, "pedestrian", {
            i: Box(i, 200.0, 200.0 + i*1.5, 220.0, 260.0 + i*1.5, "pedestrian")
            for i in range(15)
        })
    ]

    out_file = "test_calibrated_config.yaml"
    out_path = cvat_workflow.REPO_ROOT / out_file

    mock_client = MagicMock()
    clean_tracks_dict = {1: dummy_tracks[0], 2: dummy_tracks[1]}

    try:
        with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client), \
             patch("adapters.cvat_api.load_job_tracks", return_value=(clean_tracks_dict, None)):
            res = cvat_workflow.calibrate_from_jobs({
                "job_ids": [7],
                "project_id": 3,
                "project_name": "BDD100k_Test",
                "fps": 5.0,
                "out_name": out_file
            }, admin)

            assert res["ok"] is True
            assert res["config_file"] == out_file
            assert res["fps"] == 5.0
            assert "parameters" in res
            assert "max_speed" in res["parameters"]
            assert out_path.exists()
    finally:
        if out_path.exists():
            out_path.unlink()


def test_raise_single_issue():
    mock_client = MagicMock()
    mock_job = MagicMock()
    mock_job.id = 12
    mock_client.jobs.retrieve.return_value = mock_job

    reviewer = {"username": "reviewer_user", "role": "reviewer"}

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client), \
         patch.object(cvat_workflow, "load_job_tracks", return_value=({}, {})), \
         patch.object(cvat_workflow, "post_alerts_as_issues", return_value=[{"status": "created"}]) as mock_post:

        # Case 1: Flat payload
        req_flat = {
            "job_id": 12,
            "type": "id_switch_candidate",
            "track_id": 142,
            "other_id": 144,
            "frame": 45,
            "message": "Xe 142 switch sang 144"
        }
        res1 = cvat_workflow.raise_single_issue(req_flat, reviewer)
        assert res1["ok"] is True
        assert res1["alert"]["track_id"] == 142
        assert res1["alert"]["other_id"] == 144
        assert res1["alert"]["frame"] == 45
        assert "Xe 142 switch sang 144" in res1["alert"]["detail"]
        mock_post.assert_called()

        # Case 2: Nested issue object
        req_nested = {
            "job_id": 12,
            "issue": {
                "type": "Box Jump",
                "track_id": 99,
                "frame": 10,
                "message": "Box nhảy bất thường"
            }
        }
        res2 = cvat_workflow.raise_single_issue(req_nested, reviewer)
        assert res2["ok"] is True
        assert res2["alert"]["track_id"] == 99
        assert res2["alert"]["frame"] == 10
        assert "Box nhảy bất thường" in res2["alert"]["detail"]


def test_accept_job_quality_gate():
    mock_client = MagicMock()
    job = DummyJob(10, 100, stage="validation", state="completed")
    mock_client.jobs.retrieve.return_value = job

    reviewer = {"username": "reviewer_qc", "role": "reviewer"}
    admin = {"username": "admin_lead", "role": "admin"}

    issues_with_blocker = [
        {"type": "ID Switch", "frame": 5, "severity": "high", "message": "Critical ID swap"},
        {"type": "Box Jump", "frame": 10, "severity": "low", "message": "Minor jitter"},
    ]

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client), \
         patch.object(cvat_workflow, "_load_job_meta", return_value={}):

        # 1. Reviewer tries to accept with unresolved blocker -> Blocked by Quality Gate
        with pytest.raises(ValueError, match="Chốt chặn Quality Gate"):
            cvat_workflow.accept_job({"job_id": 10, "issues": issues_with_blocker}, reviewer)

        # 2. Admin tries to accept with blocker but without override reason -> Error
        with pytest.raises(ValueError, match="lý do phê duyệt ngoại lệ"):
            cvat_workflow.accept_job({"job_id": 10, "issues": issues_with_blocker, "override": True}, admin)

        # 3. Admin accepts with override reason -> Succeeded with override recorded
        res = cvat_workflow.accept_job({
            "job_id": 10,
            "issues": issues_with_blocker,
            "override": True,
            "override_reason": "Khách hàng xác nhận ngoại lệ nghiệp vụ"
        }, admin)
        assert res["ok"] is True
        assert res["stage"] == "acceptance"
        assert "ngoại lệ từ Admin" in res["message"]


def test_push_annotations_stage_guard():
    mock_client = MagicMock()
    job_val = DummyJob(11, 100, stage="validation", state="completed")
    mock_lbl = MagicMock()
    mock_lbl.id = 1
    mock_lbl.name = "Car"
    job_val.get_labels = MagicMock(return_value=[mock_lbl])
    mock_client.jobs.retrieve.return_value = job_val

    annotator = {"username": "annotator_1", "role": "annotator"}
    admin = {"username": "admin_lead", "role": "admin"}

    boxes = [{"frame_id": 0, "track_id": 1, "x": 10, "y": 10, "w": 20, "h": 20, "class_name": "Car"}]

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client):
        # Annotator cannot push when job is in validation stage
        with pytest.raises(PermissionError, match="Không thể đẩy nhãn khi Job đang ở giai đoạn 'validation'"):
            cvat_workflow.push_job_annotations({"job_id": 11, "boxes": boxes}, annotator)

        # Admin can push
        mock_client.api_client.call_api.return_value = (None, MagicMock(data=b'{}'))
        res = cvat_workflow.push_job_annotations({"job_id": 11, "boxes": boxes}, admin)
        assert res["ok"] is True


def test_boxes_to_cvat_attributes_preservation():
    shape_attrs = [{"spec_id": 10, "value": "parked"}]
    track_attrs = [{"spec_id": 20, "value": "truck"}]

    boxes = [
        {
            "frame_id": 0, "track_id": 1, "x": 10, "y": 10, "w": 20, "h": 20,
            "class_name": "Car", "attributes": shape_attrs, "track_attributes": track_attrs
        },
        {
            "frame_id": 1, "track_id": 1, "x": 12, "y": 12, "w": 20, "h": 20,
            "class_name": "Car", "attributes": shape_attrs, "track_attributes": track_attrs
        }
    ]
    label_ids = {"Car": 1}
    payload = cvat_workflow.boxes_to_cvat(boxes, label_ids, stop_frame=5)

    track = payload["tracks"][0]
    assert track["attributes"] == track_attrs
    shape = track["shapes"][0]
    assert shape["attributes"] == shape_attrs


def test_update_cvat_issue_status():
    mock_client = MagicMock()
    mock_issue = MagicMock()
    mock_issue.id = 42
    mock_issue.resolved = True
    mock_client.issues.retrieve.return_value = mock_issue

    mock_comment = MagicMock()
    mock_comment.id = 101
    mock_client.comments.create.return_value = mock_comment

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client):
        req = {
            "job_id": 5,
            "issue_id": 42,
            "resolved": True,
            "comment": "Đã sửa xong box lệch"
        }
        actor = {"username": "annotator_1", "role": "annotator"}
        res = cvat_workflow.update_cvat_issue_status(req, actor)

        assert res["ok"] is True
        assert res["resolved"] is True
        mock_issue.update.assert_called_with({"resolved": True})
        mock_client.comments.create.assert_called_with({"issue": 42, "message": "Đã sửa xong box lệch"})
        assert res["comment"]["message"] == "Đã sửa xong box lệch"
def test_open_cvat_job_annotator_only_gets_cvat_issues():
    mock_client = MagicMock()
    mock_job = DummyJob(37, 100, stage="annotation", state="rejected", assignee=DummyUser(10, "Tran_Dinh_Cuong"))
    mock_client.jobs.retrieve.return_value = mock_job

    dummy_tracks = {
        1: MagicMock(label="Car", boxes={
            0: MagicMock(x1=10, y1=10, x2=20, y2=20, outside=False, occluded=False, keyframe=True, label="Car")
        })
    }
    dummy_meta = {"frame_size": (1280, 720)}
    dummy_cvat_issues = [
        {"id": "cvat_issue:250", "cvat_issue_id": 250, "frame": 86, "message": "Box jump", "resolved": False, "is_cvat_issue": True}
    ]
    dummy_scan_res = {
        "issues": [{"id": "scan_1", "type": "Gap Frame", "frame": 5}] * 93,
        "count": 93,
        "counts": {"Gap Frame": 93},
        "stats": {}
    }

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client), \
         patch.object(cvat_workflow, "load_job_tracks", return_value=(dummy_tracks, dummy_meta)), \
         patch.object(cvat_workflow, "fetch_job_issues", return_value=dummy_cvat_issues), \
         patch.object(cvat_workflow, "scan", return_value=dummy_scan_res) as mock_scan:

        # 1. Annotator mở job -> CHỈ nhận 1 CVAT issue, KHÔNG chạy hàm scan()
        res_anno = cvat_workflow.open_cvat_job({"job_id": 37}, {"username": "Tran_Dinh_Cuong", "role": "annotator"})
        assert res_anno["effective_role"] == "annotator"
        assert len(res_anno["issues"]) == 1
        assert res_anno["count"] == 1
        assert res_anno["issues"][0]["cvat_issue_id"] == 250
        mock_scan.assert_not_called()

        # 2. Reviewer mở job khi ở stage validation -> Chạy scan để nhận toàn bộ 93 scan issues kiểm định
        mock_job.stage = "validation"
        res_rev = cvat_workflow.open_cvat_job({"job_id": 37}, {"username": "MinhHoang-123", "role": "reviewer"})
        assert res_rev["effective_role"] == "reviewer"
        assert len(res_rev["issues"]) == 93
        assert res_rev["count"] == 93
        mock_scan.assert_called_once()

        # 3. Admin mở job -> Luôn chạy scan để giám sát
        res_admin = cvat_workflow.open_cvat_job({"job_id": 37}, {"username": "admin_lead", "role": "admin"})
        assert res_admin["effective_role"] == "admin"
        assert len(res_admin["issues"]) == 93


def test_fetch_job_issues_parsing_and_box_resolution():
    mock_job = MagicMock()
    mock_iss = MagicMock()
    mock_iss.id = 318
    mock_iss.frame = 7
    mock_iss.position = [0.0, 0.0, 100.0, 100.0]
    mock_iss.resolved = False
    mock_comment = MagicMock()
    mock_comment.author = MagicMock(username="Reviewer_Bot")
    mock_comment.message = "[CVAT_ISSUE] Track: [CVAT_ISSUE] Track: [ID_SWITCH_CANDIDATE] Track #12 & #12: nghi hoán ID giữa track 12 và 13"
    mock_comment.created_date = "2026-10-18T10:00:00Z"
    mock_iss.get_comments.return_value = [mock_comment]
    mock_job.get_issues.return_value = [mock_iss]

    issues = cvat_workflow.fetch_job_issues(mock_job)
    assert len(issues) == 1
    iss = issues[0]
    assert iss["track_id"] == 12
    assert iss["type"] == "Id Switch Candidate"
    assert iss["message"] == "[ID_SWITCH_CANDIDATE] Track #12 & #12: nghi hoán ID giữa track 12 và 13"

    # Test open_cvat_job resolves dummy position with real track box
    mock_client = MagicMock()
    mock_client.jobs.retrieve.return_value = mock_job
    mock_job.task_id = 37
    mock_job.stage = "annotation"
    mock_job.state = "rejected"
    mock_job.assignee = MagicMock(username="Tran_Dinh_Cuong")
    mock_job.start_frame = 0
    mock_job.stop_frame = 200
    mock_job.frame_count = 201

    tr12 = Track(id=12, label="pedestrian")
    tr12.boxes[7] = Box(7, 981.6, 297.27, 1000.63, 378.13, "pedestrian")
    tracks = {12: tr12}

    with patch.object(cvat_workflow, "_get_active_client", return_value=mock_client), \
         patch.object(cvat_workflow, "load_job_tracks", return_value=(tracks, {"frame_size": (1280, 720)})), \
         patch.object(cvat_workflow, "fetch_job_issues", return_value=issues):

        res = cvat_workflow.open_cvat_job({"job_id": 37}, {"username": "Tran_Dinh_Cuong", "role": "annotator"})
        assert len(res["issues"]) == 1
        resolved_pos = res["issues"][0]["position"]
        # Position should now be track 12's box, NOT [0, 0, 100, 100]
        assert resolved_pos == [981.6, 297.3, 1000.6, 378.1]

