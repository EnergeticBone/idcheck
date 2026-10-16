"""Local, append-only QA audit history; no authentication, loopback use only."""
import datetime
import json
from pathlib import Path
import secrets
import threading

PATH=Path(__file__).resolve().parents[1]/'storage'/'audit.jsonl'
LOCK=threading.Lock()


def new_session():
    return secrets.token_urlsafe(16)


def record(session,action,detail=None):
    if not isinstance(session,str) or len(session)<12 or len(session)>100:
        raise ValueError('Session ID không hợp lệ.')
    entry={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'session':session,'action':str(action)[:80],'detail':detail or {}}
    PATH.parent.mkdir(parents=True,exist_ok=True)
    with LOCK,PATH.open('a',encoding='utf-8') as f:
        f.write(json.dumps(entry,ensure_ascii=False,default=str)+'\n')
    return entry


def read(session,limit=200):
    if not PATH.exists():return []
    with LOCK,PATH.open(encoding='utf-8') as f:
        rows=[]
        for line in f:
            try:
                entry=json.loads(line)
                if entry.get('session')==session:rows.append(entry)
            except json.JSONDecodeError:pass
    return rows[-max(1,min(int(limit),500)):]
