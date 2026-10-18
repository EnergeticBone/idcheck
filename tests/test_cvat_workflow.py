from unittest.mock import MagicMock, patch
import pytest

from studio.backend import cvat_workflow


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

    # 2. Test calibrate_from_jobs permission check
    worker = {"username": "annotator_cuong", "role": "worker"}
    supervisor = {"username": "qa_lead", "role": "supervisor"}

    with pytest.raises(PermissionError):
        cvat_workflow.calibrate_from_jobs({"job_ids": [7]}, worker)

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
            }, supervisor)

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



