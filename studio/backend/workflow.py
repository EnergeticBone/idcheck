"""Durable issue handoff: reviewer QC -> annotator fix -> reviewer verification."""
from backend import projects
from backend.audit import record

STATES = {'needs_fix', 'false_positive', 'fixed', 'verified'}

def list_tasks(req, actor):
    p = projects.get(req.get('project_id'))
    return {'tasks': p.get('tasks', {}), 'project_id': p['id']}

def transition(req, actor):
    from backend.auth import require
    issue_id = str(req.get('issue_id', '')).strip()
    new_state = str(req.get('status', '')).strip()
    note = str(req.get('note', '')).strip()
    if not issue_id or len(issue_id) > 160 or new_state not in STATES:
        raise ValueError('Issue ID hoặc trạng thái không hợp lệ')
    if len(note) > 1500:
        raise ValueError('Ghi chú tối đa 1.500 ký tự')
    with projects.LOCK:
        p = projects.get(req.get('project_id'))
        tasks = p.setdefault('tasks', {})
        old = tasks.get(issue_id, {})
        prev = old.get('status')
        role = actor['role']
        if new_state in ('needs_fix', 'false_positive'):
            require(actor, 'reviewer')
            if new_state == 'needs_fix' and not note:
                raise ValueError('Reviewer phải ghi rõ lỗi và yêu cầu sửa')
            if prev == 'verified':
                raise ValueError('Issue đã xác nhận hoàn tất; hãy tạo vòng QC mới')
        elif new_state == 'fixed':
            require(actor, 'annotator')
            if int(p.get('revision',0))<=int(old.get('requested_revision',0)):
                raise ValueError('Chưa có thay đổi annotation được lưu sau yêu cầu QC. Hãy sửa và lưu project trước.')
            if prev != 'needs_fix':
                raise ValueError('Chỉ Annotator mới đánh dấu Đã sửa cho yêu cầu cần sửa')
            if not note:
                raise ValueError('Annotator cần ghi nội dung đã chỉnh sửa')
        elif new_state == 'verified':
            require(actor, 'reviewer')
            if prev != 'fixed':
                raise ValueError('Reviewer chỉ xác nhận khi Annotator đã báo sửa xong')
            if int(p.get('revision',0))!=int(old.get('fixed_revision',-1)):
                raise ValueError('Annotation đã tiếp tục thay đổi sau khi Annotator báo sửa; cần gửi lại QC.')
        if new_state == 'false_positive' and prev == 'fixed':
            raise ValueError('Annotator đã sửa; Reviewer cần xác nhận hoặc yêu cầu sửa tiếp')
        issue = req.get('issue') if isinstance(req.get('issue'), dict) else {}
        task = {'issue_id': issue_id, 'status': new_state,
                'fixed_revision': int(p.get('revision',0)) if new_state=='fixed' else old.get('fixed_revision'),
                'type': str(issue.get('type', old.get('type', 'Unknown')))[:120],
                'frame': int(issue.get('frame', old.get('frame', 0))),
                'track_id': issue.get('track_id', old.get('track_id')),
                'message': str(issue.get('message', old.get('message', '')))[:500],
                'note': note, 'updated_by': actor['username'], 'updated_at': projects.now(),
                'requested_revision': int(p.get('revision',0)) if new_state=='needs_fix' else old.get('requested_revision',0),
                'history': old.get('history', []) + [{'from':prev,'to':new_state,'by':actor['username'],
                                                    'note':note,'at':projects.now()}]}
        tasks[issue_id] = task
        p['updated_at'] = projects.now()
        projects._write(p)
        record(p['session'], 'workflow', {'issue_id':issue_id,'old':prev,'new':new_state,
                                          'actor': actor['username'],'role':role, 'note':note})
        return {'task': task, 'tasks': tasks}
