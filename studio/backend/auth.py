"""Authentication and Role Enforcement for idcheck Studio.

Supports direct CVAT Single Sign-On (SSO):
- Users log in using their CVAT username & password directly.
- Roles are automatically resolved from CVAT (is_staff -> admin, supervisor -> reviewer, worker -> annotator).
- Lead / Admin can assign and override roles directly in the Web App (persisted in cvat_roles.json).
- Local fallback accounts in users.json supported for offline/demo operation.
"""
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time
import urllib3

from cvat_sdk.core.client import Client, Config
from cvat_sdk.api_client.api import memberships_api

logger = logging.getLogger(__name__)

ROOT_STORAGE = Path(__file__).resolve().parents[1] / 'storage'
USERS_FILE = ROOT_STORAGE / 'users.json'
SESSIONS_FILE = ROOT_STORAGE / 'sessions.json'
CVAT_ROLES_FILE = ROOT_STORAGE / 'cvat_roles.json'
LOCK = threading.RLock()
SESSION_SECONDS = 12 * 3600
ROLES = {'admin', 'annotator', 'reviewer'}


def _load_sessions():
    if not SESSIONS_FILE.exists():
        return {}
    try:
        now = time.time()
        sessions = json.loads(SESSIONS_FILE.read_text(encoding='utf-8'))
        return {k: v for k, v in sessions.items() if v.get('expiry', 0) > now}
    except Exception:
        return {}


def _write_sessions(sessions):
    try:
        SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SESSIONS_FILE.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(sessions, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        tmp.replace(SESSIONS_FILE)
    except Exception:
        pass


SESSIONS = _load_sessions()


def _load():
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _write(accounts):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(accounts, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    tmp.replace(USERS_FILE)


def _load_cvat_roles():
    if not CVAT_ROLES_FILE.exists():
        return {}
    try:
        return json.loads(CVAT_ROLES_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_cvat_roles(roles_dict):
    CVAT_ROLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CVAT_ROLES_FILE.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(roles_dict, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(CVAT_ROLES_FILE)


def _key(username):
    name = str(username).strip()
    if not 2 <= len(name) <= 60:
        raise ValueError('Tên tài khoản dài 2–60 ký tự')
    return name


def _password(value):
    if not isinstance(value, str) or len(value) < 4:
        raise ValueError('Mật khẩu không hợp lệ')
    return value


def _hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 280_000).hex()


def _try_cvat_login(username, password):
    """Xác thực trực tiếp với CVAT qua API."""
    host = os.environ.get("CVAT_HOST", "https://cvat.trantuli.id.vn")
    insecure = os.environ.get("CVAT_INSECURE", "1").lower() in ("1", "true", "yes")
    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        config = Config(verify_ssl=False)
    else:
        config = None

    try:
        client = Client(url=host, config=config)
        client.login((username, password))
        me, _ = client.users.api.retrieve_self()

        # Kiểm tra vai trò trong Organization (nếu có)
        org_role = None
        try:
            m_api = memberships_api.MembershipsApi(client.api_client)
            m_res, _ = m_api.list(user=me.username)
            if m_res and m_res.results:
                org_role = m_res.results[0].role
        except Exception:
            pass

        return {
            'id': me.id,
            'username': me.username,
            'email': getattr(me, 'email', ''),
            'is_staff': getattr(me, 'is_staff', False),
            'groups': getattr(me, 'groups', []) or [],
            'org_role': org_role,
        }
    except Exception as exc:
        logger.debug("CVAT login failed for %s: %s", username, exc)
        return None


def status():
    return {
        'needs_setup': False,
        'roles': sorted(ROLES),
        'cvat_host': os.environ.get("CVAT_HOST", "https://cvat.trantuli.id.vn"),
    }


def setup(req):
    with LOCK:
        if _load():
            raise ValueError('Đã có tài khoản quản trị, không thể khởi tạo lại')
        name = _key(req.get('username'))
        password = _password(req.get('password'))
        salt = secrets.token_hex(16)
        _write({name: {'username': name, 'role': 'admin', 'salt': salt, 'password_hash': _hash(password, salt)}})
        return {'created': True, 'username': name, 'role': 'admin'}


def login(req):
    with LOCK:
        name = _key(req.get('username'))
        password = str(req.get('password', ''))

        # 1. Thử xác thực với máy chủ CVAT trước (Ưu tiên CVAT SSO)
        cvat_user = _try_cvat_login(name, password)
        if cvat_user is not None:
            uname = cvat_user['username']
            cvat_roles = _load_cvat_roles()

            # Phân quyền:
            # - Nếu Lead đã gán vai trò riêng trong cvat_roles.json -> ưu tiên dùng vai trò đó
            # - Nếu là is_staff hoặc group admin hoặc org owner/maintainer -> admin
            # - Nếu org supervisor hoặc group reviewer -> reviewer
            # - Mặc định -> annotator
            if uname in cvat_roles:
                role = cvat_roles[uname]
            elif cvat_user['is_staff'] or 'admin' in cvat_user['groups'] or cvat_user.get('org_role') in ('owner', 'maintainer'):
                role = 'admin'
            elif cvat_user.get('org_role') == 'supervisor' or 'reviewer' in cvat_user['groups']:
                role = 'reviewer'
            else:
                role = 'annotator'

            token = secrets.token_urlsafe(32)
            SESSIONS[token] = {
                'username': uname,
                'role': role,
                'source': 'cvat',
                'cvat_id': cvat_user['id'],
                'expiry': time.time() + SESSION_SECONDS
            }
            _write_sessions(SESSIONS)
            return {'token': token, 'user': {'username': uname, 'role': role, 'source': 'cvat'}}

        # 2. Nếu không đăng nhập được qua CVAT, kiểm tra tài khoản local dự phòng
        user = _load().get(name)
        if user and hmac.compare_digest(_hash(password, user['salt']), user['password_hash']):
            token = secrets.token_urlsafe(32)
            SESSIONS[token] = {'username': name, 'role': user['role'], 'source': 'local', 'expiry': time.time() + SESSION_SECONDS}
            _write_sessions(SESSIONS)
            return {'token': token, 'user': {'username': name, 'role': user['role'], 'source': 'local'}}

        raise ValueError('Tài khoản hoặc mật khẩu CVAT không chính xác')


def verify(token):
    with LOCK:
        session = SESSIONS.get(token or '')
        if not session or session['expiry'] < time.time():
            if token in SESSIONS:
                SESSIONS.pop(token, None)
                _write_sessions(SESSIONS)
            raise PermissionError('Phiên đăng nhập không hợp lệ hoặc hết hạn; hãy đăng nhập lại')
        return {
            'username': session['username'],
            'role': session['role'],
            'source': session.get('source', 'cvat'),
        }


def logout(token):
    with LOCK:
        SESSIONS.pop(token or '', None)
        _write_sessions(SESSIONS)
    return {'ok': True}


def create_user(req, actor):
    require(actor, 'admin')
    name = _key(req.get('username'))
    password = _password(req.get('password'))
    role = req.get('role')
    if role not in ('annotator', 'reviewer'):
        raise ValueError('Chỉ tạo tài khoản Annotator hoặc Reviewer')
    with LOCK:
        users = _load()
        if name in users:
            raise ValueError('Tài khoản đã tồn tại')
        salt = secrets.token_hex(16)
        users[name] = {'username': name, 'role': role, 'salt': salt, 'password_hash': _hash(password, salt)}
        _write(users)
    return {'created': True, 'username': name, 'role': role}


def list_users(_, actor):
    require(actor, 'admin')
    return {'users': [{'username': u['username'], 'role': u['role']} for u in _load().values()]}


def list_cvat_users_with_roles(actor):
    """Liệt kê danh sách người dùng CVAT kèm vai trò hiện tại trong Studio."""
    require(actor, 'admin')
    from adapters.cvat_api import get_client
    client = get_client()

    raw_users = client.users.list()
    cvat_roles = _load_cvat_roles()

    # Lấy danh sách membership nếu có
    user_org_roles = {}
    try:
        m_api = memberships_api.MembershipsApi(client.api_client)
        m_res, _ = m_api.list()
        for m in (m_res.results if m_res else []):
            if getattr(m, 'user', None) and getattr(m.user, 'username', None):
                user_org_roles[m.user.username] = m.role
    except Exception:
        pass

    result = []
    for u in raw_users:
        uname = u.username
        org_r = user_org_roles.get(uname)
        groups = getattr(u, 'groups', []) or []
        is_staff = getattr(u, 'is_staff', False)

        # Quyết định vai trò hiện tại
        if uname in cvat_roles:
            assigned = cvat_roles[uname]
        elif is_staff or 'admin' in groups or org_r in ('owner', 'maintainer'):
            assigned = 'admin'
        elif org_r == 'supervisor' or 'reviewer' in groups:
            assigned = 'reviewer'
        else:
            assigned = 'annotator'

        result.append({
            'id': u.id,
            'username': uname,
            'is_staff': is_staff,
            'groups': groups,
            'org_role': org_r,
            'role': assigned,
        })

    return {'users': result, 'roles': sorted(ROLES)}


def set_user_role(req, actor):
    """Lead phân vai trò Annotator hoặc Reviewer cho tài khoản CVAT."""
    require(actor, 'admin')
    username = _key(req.get('username'))
    role = str(req.get('role', '')).strip()
    if role not in ROLES:
        raise ValueError(f'Vai trò không hợp lệ: {role}')

    with LOCK:
        cvat_roles = _load_cvat_roles()
        cvat_roles[username] = role
        _save_cvat_roles(cvat_roles)

        # Cập nhật ngay các phiên đang chạy nếu có
        for s in SESSIONS.values():
            if s.get('username') == username:
                s['role'] = role
        _write_sessions(SESSIONS)

    return {'ok': True, 'username': username, 'role': role}


def require(actor, *roles):
    if actor['role'] not in roles:
        raise PermissionError('Không đủ quyền: yêu cầu vai trò ' + '/'.join(roles))
