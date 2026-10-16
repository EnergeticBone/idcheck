import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argparse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import logging
from http.cookies import SimpleCookie
import threading
import webbrowser
from urllib.parse import urlsplit, unquote
from backend.orchestrator import ROUTES
from studio_core.parsers import parse_cvat_archive
from backend.media import STORE, MAX_ZIP_BYTES
from backend.video import VIDEO, MAX_VIDEO_BYTES
from backend import projects, auth, workflow, cvat_workflow

ROOT=Path(__file__).resolve().parent
STATIC=ROOT/'static'
MAX=60_000_000
PROJECT_ROUTES = {'/api/projects/list':projects.list_projects,'/api/projects/create':projects.create,
                  '/api/projects/save':projects.save,'/api/projects/open':projects.open_project,
                  '/api/projects/rename':projects.rename,'/api/projects/delete':projects.delete}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(STATIC),**kwargs)
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'")
        super().end_headers()
    def json(self,obj,code=200,headers=None):
        raw=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        for name,value in (headers or {}).items():self.send_header(name,value)
        self.end_headers();self.wfile.write(raw)
    def actor(self):
        header = self.headers.get('Authorization', '')
        if header.startswith('Bearer '):return auth.verify(header[7:])
        cookies=SimpleCookie()
        try:cookies.load(self.headers.get('Cookie',''))
        except Exception:pass
        token=cookies['t021session'].value if 't021session' in cookies else ''
        return auth.verify(token)
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/api/auth/status':
            return self.json(auth.status())
        if path=='/api/health':
            return self.json({'ok':True,'name':'T021 Track ID QA','version':'2.5.0-cvat-import'})
        if path.startswith('/api/video/') or path.startswith('/api/media/'):
            try:self.actor()
            except PermissionError as exc:return self.json({'error':str(exc)},403)
        if path=='/api/video/diagnostics':
            return self.json({'ok':True,'version':'2.5.0-cvat-import','ffmpeg':VIDEO.diagnostics()})
        if path.startswith('/api/video/'):
            # /api/video/{token}/frame/{source_frame_index}
            parts=path.split('/')
            if len(parts)!=6 or parts[4]!='frame' or not parts[5].isdigit():
                return self.json({'error':'Đường dẫn frame video không hợp lệ'},404)
            try:
                data=VIDEO.frame(parts[3],int(parts[5]))
            except ValueError as exc:
                return self.json({'error':str(exc)},422)
            self.send_response(200)
            self.send_header('Content-Type','image/jpeg')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
            return
        if path.startswith('/api/media/'):
            parts = path.split('/')
            if len(parts)!=5 or not parts[4].isdigit():
                return self.json({'error':'Đường dẫn ảnh không hợp lệ'},404)
            img = STORE.get(parts[3], int(parts[4]))
            if img is None:
                return self.json({'error':'Ảnh không tồn tại hoặc phiên media hết hạn'},404)
            data, mime = img
            self.send_response(200)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith('/api/cvat/frame/'):
            # /api/cvat/frame/{job_id}/{frame_id}
            try:self.actor()
            except PermissionError as exc:return self.json({'error':str(exc)},403)
            parts=path.split('/')
            if len(parts)!=6 or not parts[4].isdigit() or not parts[5].isdigit():
                return self.json({'error':'Đường dẫn frame CVAT không hợp lệ'},404)
            try:
                data=cvat_workflow.get_job_frame(int(parts[4]),int(parts[5]))
            except Exception as exc:
                return self.json({'error':f'Không thể tải frame từ CVAT: {exc}'},500)
            self.send_response(200)
            self.send_header('Content-Type','image/jpeg')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
            return
        return super().do_GET()
    def do_POST(self):
        route=urlsplit(self.path).path
        auth_routes={'/api/auth/setup','/api/auth/login','/api/auth/logout',
                     '/api/auth/create-user','/api/auth/users','/api/auth/me',
                     '/api/auth/cvat-users','/api/auth/set-role'}
        workflow_routes={'/api/workflow/list','/api/workflow/transition'}
        cvat_routes={'/api/cvat/jobs','/api/cvat/job/submit-validation','/api/cvat/job/open',
                     '/api/cvat/job/reject-and-raise','/api/cvat/job/accept','/api/cvat/job/assign','/api/cvat/users'}
        if route not in ('/api/media/upload-zip','/api/video/upload','/api/annotation/import') and route not in ROUTES and route not in PROJECT_ROUTES and route not in auth_routes and route not in workflow_routes and route not in cvat_routes:
            return self.json({'error':'Không tìm thấy API'},404)
        try:
            actor = None if route in ('/api/auth/setup','/api/auth/login') else self.actor()
            n=int(self.headers.get('Content-Length',0))
            if route == '/api/video/upload':
                if not 0 < n <= MAX_VIDEO_BYTES:
                    raise ValueError('Video rỗng hoặc lớn hơn 300 MB')
                filename=unquote(self.headers.get('X-T021-Filename','video.mp4'))
                return self.json(VIDEO.add(self.rfile,n,filename))
            if route == '/api/annotation/import':
                if not 0 < n <= MAX_ZIP_BYTES:
                    raise ValueError('ZIP annotation rỗng hoặc vượt 100 MB')
                filename=unquote(self.headers.get('X-T021-Filename','annotations.zip'))
                # Import only returns a parsed, transient preview. It never saves project state.
                return self.json(parse_cvat_archive(filename,self.rfile.read(n)))
            if route == '/api/media/upload-zip':
                if not 0 < n <= MAX_ZIP_BYTES:
                    raise ValueError('ZIP rỗng hoặc lớn hơn 100 MB')
                if self.headers.get('Content-Type','').split(';')[0] not in ('application/zip','application/octet-stream'):
                    raise ValueError('Cần gửi dữ liệu ZIP nhị phân')
                result = STORE.upload_zip(self.rfile.read(n),
                    self.headers.get('X-T021-Task',''),
                    self.headers.get('X-T021-Frame-Count','') or None)
                return self.json(result)

            if not 0<n<=MAX:raise ValueError('Request rỗng hoặc vượt 60 MB')
            req=json.loads(self.rfile.read(n))
            if not isinstance(req,dict):raise ValueError('Request body phải là JSON object')
            if route=='/api/auth/setup':return self.json(auth.setup(req))
            if route=='/api/auth/login':
                result=auth.login(req)
                return self.json({'user':result['user']},headers={'Set-Cookie':
                     f't021session={result["token"]}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'})
            if route=='/api/auth/logout':
                cookies=SimpleCookie();cookies.load(self.headers.get('Cookie',''))
                token=cookies['t021session'].value if 't021session' in cookies else ''
                result=auth.logout(token)
                return self.json(result,headers={'Set-Cookie':'t021session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
            if route=='/api/auth/me':return self.json({'user':actor})
            if route=='/api/auth/create-user':return self.json(auth.create_user(req,actor))
            if route=='/api/auth/users':return self.json(auth.list_users(req,actor))
            if route=='/api/auth/cvat-users':return self.json(auth.list_cvat_users_with_roles(actor))
            if route=='/api/auth/set-role':return self.json(auth.set_user_role(req,actor))
            if route=='/api/workflow/list':return self.json(workflow.list_tasks(req,actor))
            if route=='/api/workflow/transition':return self.json(workflow.transition(req,actor))
            if route=='/api/cvat/jobs':return self.json(cvat_workflow.list_jobs(actor))
            if route=='/api/cvat/job/submit-validation':return self.json(cvat_workflow.submit_for_validation(req,actor))
            if route=='/api/cvat/job/open':return self.json(cvat_workflow.open_cvat_job(req,actor))
            if route=='/api/cvat/job/reject-and-raise':return self.json(cvat_workflow.reject_and_raise_issues(req,actor))
            if route=='/api/cvat/job/accept':return self.json(cvat_workflow.accept_job(req,actor))
            if route=='/api/cvat/job/assign':return self.json(cvat_workflow.assign_job(req,actor))
            if route=='/api/cvat/users':return self.json(cvat_workflow.list_cvat_users(actor))
            if route=='/api/fix':auth.require(actor,'annotator')
            if route=='/api/review':raise ValueError('Dùng /api/workflow/transition để QC và chuyển lỗi cho Annotator')
            if route in ('/api/projects/create','/api/projects/save'):auth.require(actor,'annotator')
            if route in ('/api/projects/rename','/api/projects/delete'):auth.require(actor,'annotator','admin')
            if route=='/api/projects/save':
                # Review decisions are stored by server workflows, never trusted from annotator/browser state.
                req['state']=dict(req.get('state') or {},decisions={})
            if route=='/api/export':auth.require(actor,'annotator')
            return self.json((PROJECT_ROUTES if route in PROJECT_ROUTES else ROUTES)[route](req))
        except PermissionError as exc:
            return self.json({'error':str(exc)},403)
        except (ValueError,TypeError,KeyError,IndexError) as exc:
            return self.json({'error':str(exc)},400)
        except Exception:
            logging.exception('T021 server error')
            return self.json({'error':'Lỗi nội bộ máy chủ (xem Terminal)'},500)


def main():
    ap = argparse.ArgumentParser(description="idcheck QA Studio Server")
    ap.add_argument('--host', type=str, default='127.0.0.1', help='Host to bind (127.0.0.1 or 0.0.0.0)')
    ap.add_argument('--port', type=int, default=8765, help='Port to listen on')
    ap.add_argument('--no-browser', action='store_true', help='Do not open browser automatically')
    args = ap.parse_args()
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        raise SystemExit(f'Không mở được cổng {args.port} trên {args.host}: {exc}. Thử cổng khác với --port.')
    url = f'http://{args.host}:{args.port}'
    print(f'\n======================================================', flush=True)
    print(f' idcheck QA Studio (powered by idcheck core rules)   ', flush=True)
    print(f' URL: {url}                                          ', flush=True)
    print(f' Dừng server bằng phím Ctrl+C                         ', flush=True)
    print(f'======================================================\n', flush=True)
    if not args.no_browser and args.host in ('127.0.0.1', 'localhost'):
        threading.Timer(.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

if __name__=='__main__':main()
