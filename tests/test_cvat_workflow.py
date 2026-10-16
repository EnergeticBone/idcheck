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

