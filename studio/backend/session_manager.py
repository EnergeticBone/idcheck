"""Portable project snapshots. Media is referenced by filename, never embedded."""
import json
from studio_core.models import normalize_boxes


def parse_project(text):
    try:src=json.loads(text)
    except json.JSONDecodeError as exc:raise ValueError('Project JSON không hợp lệ') from exc
    if not isinstance(src,dict) or src.get('project_type')!='t021_project':
        raise ValueError('JSON không phải T021 project. Chỉ tải .t021.json đã lưu từ ứng dụng.')
    m=src.get('meta')
    if not isinstance(m,dict):raise ValueError('Project thiếu metadata')
    b=normalize_boxes(src.get('boxes',[]))
    if not b:raise ValueError('Project không có box')
    decisions=src.get('decisions',{})
    if not isinstance(decisions,dict):decisions={}
    return dict(boxes=b,format=m.get('format','mot'),frame_base=int(m.get('frame_base',1)),
                mode=m.get('mode','video'),width=m.get('width'),height=m.get('height'),
                total_frames=m.get('total_frames'),labels=m.get('labels',[]),
                decisions={str(k):str(v) for k,v in decisions.items()},
                warnings=['Đã khôi phục nhãn và quyết định reviewer. Ảnh/video cần chọn lại bằng tay.'],
                source_name=src.get('source_name','T021 project'))
