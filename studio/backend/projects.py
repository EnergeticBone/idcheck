"""Filesystem-backed local project workspace. Atomic JSON snapshots, no SQL needed."""
import datetime
import hashlib
import json
import re
import secrets
import shutil
import threading
from pathlib import Path
from backend.audit import new_session
from backend.video import VIDEO

ROOT = Path(__file__).resolve().parents[1] / 'storage' / 'projects'
ID_RE = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
LOCK = threading.RLock()
MAX_BOXES = 200000

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def path_for(project_id):
    if not isinstance(project_id,str) or not ID_RE.fullmatch(project_id):
        raise ValueError('Project ID không hợp lệ')
    return ROOT/project_id/'project.json'

def get(project_id):
    path = path_for(project_id)
    if not path.exists():
        raise ValueError('Không tìm thấy project')
    return json.loads(path.read_text(encoding='utf-8'))

def _write(data):
    path = path_for(data['id'])
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = path.with_suffix('.json.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,allow_nan=False)
    temp.replace(path)

def list_projects(_=None):
    with LOCK:
        rows=[]
        if ROOT.exists():
            for p in ROOT.glob('*/project.json'):
                try:
                    d=json.loads(p.read_text(encoding='utf-8'))
                    rows.append({k:d.get(k) for k in ('id','name','created_at','updated_at','source_name','box_count','media_kind','frame_count')})
                except (OSError,ValueError):pass
        rows.sort(key=lambda r:r.get('updated_at') or '',reverse=True)
        return {'projects':rows}

def create(req):
    name = str(req.get('name','')).strip()
    if not 1<=len(name)<=100:raise ValueError('Tên project dài 1–100 ký tự')
    with LOCK:
        project_id = secrets.token_urlsafe(12)
        date = now()
        d=dict(id=project_id,name=name,created_at=date,updated_at=date,source_name='',
               box_count=0,frame_count=0,media_kind='none',state={},revision=0,tasks={},session=new_session())
        _write(d)
        return {'project':d}

def save(req):
    with LOCK:
        d=get(req.get('id'))
        state=req.get('state')
        if not isinstance(state,dict):raise ValueError('Thiếu state của project')
        boxes=state.get('boxes',[])
        if not isinstance(boxes,list) or len(boxes)>MAX_BOXES:
            raise ValueError('Số lượng box không hợp lệ / vượt 200.000')
        decisions=state.get('decisions',{})
        if not isinstance(decisions,dict):raise ValueError('decisions không hợp lệ')
        meta=state.get('meta',{})
        if not isinstance(meta,dict):raise ValueError('meta không hợp lệ')
        media=state.get('media',{})
        if not isinstance(media,dict):raise ValueError('media không hợp lệ')
        kind=media.get('kind','none')
        if kind not in ('none','video','zip','images'):raise ValueError('media kind không hợp lệ')
        if kind=='video':VIDEO.metadata(media.get('token'))
        if kind=='zip':
            from backend.media import STORE
            STORE.check_token(media.get('token'))
        if kind=='images':
            # Loose files use ephemeral object URLs: never promise they persist.
            media={'kind':'none','warning':'Ảnh rời không được lưu; hãy tải frames.zip để lưu cùng project.'}
            kind='none'
        # Do not use client-supplied session identifier for audit storage.
        state=dict(boxes=boxes,meta=meta,decisions={},source_name=str(state.get('source_name',''))[:255],
                   frame=state.get('frame',0),fps=state.get('fps',30),video_start=state.get('video_start',0),
                   video_step=state.get('video_step',1),image_mapping=state.get('image_mapping','auto'),
                   media=media)
        # Increment annotation revision only when the saved boxes actually change.
        previous=json.dumps(d.get('state',{}).get('boxes',[]),sort_keys=True,separators=(',',':'))
        current=json.dumps(boxes,sort_keys=True,separators=(',',':'))
        if previous!=current:d['revision']=int(d.get('revision',0))+1
        d.update(state=state,updated_at=now(),source_name=state['source_name'],box_count=len(boxes),
                 frame_count=int(meta.get('total_frames') or 0),media_kind=kind)
        _write(d)
        return {'project':{k:d[k] for k in ('id','name','updated_at','box_count','media_kind')}}

def open_project(req):
    with LOCK:
        d=get(req.get('id'))
        state=d.get('state',{})
        media=state.get('media',{})
        if media.get('kind')=='video':VIDEO.metadata(media.get('token'))
        if media.get('kind')=='zip':
            from backend.media import STORE
            STORE.check_token(media.get('token'))
        return {'project':d}

def rename(req):
    name=str(req.get('name','')).strip()
    if not 1<=len(name)<=100:raise ValueError('Tên project dài 1–100 ký tự')
    with LOCK:
        d=get(req.get('id'));d['name']=name;d['updated_at']=now();_write(d)
        return {'project':{'id':d['id'],'name':name}}

def delete(req):
    with LOCK:
        d=get(req.get('id'))
        shutil.rmtree(path_for(d['id']).parent)
        return {'deleted':True,'id':d['id']}
