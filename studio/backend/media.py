"""Local-only, bounded, in-memory ZIP image store for annotation frame viewing.

ZIP images are sorted by filename, but mapped to annotation frame IDs by the
browser using an explicit mapping mode. ZIP filename suffixes are *not* assumed
to be frame indices (sampled source filenames often skip numbers).
"""
import io
import re
from pathlib import Path
import mimetypes
import secrets
import threading
import time
import zipfile
from collections import OrderedDict
from pathlib import PurePosixPath

ZIP_ROOT = Path(__file__).resolve().parents[1] / 'storage' / 'zip_cache'
ZIP_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{16,64}$')
MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 160 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_COUNT = 2500
MAX_SESSIONS = 3
TTL_SECONDS = 2 * 60 * 60
MIME = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
        '.webp': 'image/webp', '.gif': 'image/gif'}


class MediaStore:
    def __init__(self):
        self.sessions = OrderedDict()
        self.lock = threading.RLock()

    def _cleanup(self):
        now = time.monotonic()
        for token, entry in list(self.sessions.items()):
            if now - entry['created'] > TTL_SECONDS:
                del self.sessions[token]
        while len(self.sessions) > MAX_SESSIONS:
            self.sessions.popitem(last=False)

    def upload_zip(self, raw: bytes, task_name: str = '', frame_count=None):
        if not raw or len(raw) > MAX_ZIP_BYTES:
            raise ValueError('ZIP rỗng hoặc vượt giới hạn 100 MB.')
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except (zipfile.BadZipFile, OSError) as exc:
            raise ValueError('File không phải ZIP hợp lệ.') from exc
        with archive:
            entries = []
            for info in archive.infolist():
                name = PurePosixPath(info.filename.replace('\\', '/')).name
                suffix = PurePosixPath(name).suffix.lower()
                if info.is_dir() or name.startswith('.') or '__MACOSX' in info.filename or suffix not in MIME:
                    continue
                if info.flag_bits & 0x1:
                    raise ValueError('ZIP chứa ảnh được mã hóa; hãy giải nén và nén lại không mật khẩu.')
                if info.file_size > MAX_IMAGE_BYTES:
                    raise ValueError(f'Ảnh {name} lớn hơn 20 MB.')
                entries.append((name, info, suffix))
            if not entries:
                raise ValueError('ZIP không có ảnh JPG, PNG, WebP hoặc GIF.')
            if len(entries) > MAX_IMAGE_COUNT:
                raise ValueError('ZIP vượt giới hạn 2.500 ảnh. Hãy chia thành nhiều sequence.')
            entries.sort(key=lambda e: natural_sort_key(e[0]))
            total = sum(e[1].file_size for e in entries)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError('Tổng ảnh giải nén vượt 160 MB, hãy chia nhỏ ZIP.')
            names = [entry[0] for entry in entries]
            if len(set(names)) != len(names):
                raise ValueError('ZIP có tên ảnh trùng nhau sau khi bỏ đường dẫn thư mục.')
            images = []
            for name, info, suffix in entries:
                data = archive.read(info)
                # Detect obviously non-image payloads; browser will decode/verify pixels.
                sig_ok = (data.startswith(b'\xff\xd8\xff') if suffix in ('.jpg','.jpeg')
                          else data.startswith(b'\x89PNG\r\n\x1a\n') if suffix == '.png'
                          else data.startswith(b'RIFF') and data[8:12] == b'WEBP' if suffix == '.webp'
                          else data.startswith((b'GIF87a', b'GIF89a')))
                if not sig_ok:
                    raise ValueError(f'Ảnh {name} có nội dung không khớp phần mở rộng.')
                images.append((data, MIME[suffix]))
        warnings = []
        prefix = task_name.strip().lower()
        if prefix and not any(prefix in name.lower() for name in names):
            warnings.append(f'Tên ảnh không chứa tên task XML "{task_name}". Kiểm tra ZIP có thuộc đúng sequence hay không.')
        if frame_count and int(frame_count) != len(images):
            warnings.append(f'XML mô tả {frame_count} frame, ZIP có {len(images)} ảnh. Hãy chọn kiểu ghép frame phù hợp.')
        token = secrets.token_urlsafe(18)
        with self.lock:
            self._cleanup()
            ZIP_ROOT.mkdir(parents=True,exist_ok=True)
            (ZIP_ROOT / (token + '.zip')).write_bytes(raw)
            self.sessions[token] = {'created': time.monotonic(), 'images': images}
            self._cleanup()
        return {'token': token, 'files': names, 'count': len(images), 'warnings': warnings}

    def check_token(self, token):
        if not isinstance(token,str) or not ZIP_TOKEN_RE.fullmatch(token):
            raise ValueError('Token ảnh ZIP không hợp lệ')
        if not (ZIP_ROOT / (token + '.zip')).exists():
            raise ValueError('Không tìm thấy ZIP đã tải lên; hãy tải lại ZIP.')
        return True

    def restore_token(self, token):
        self.check_token(token)
        with self.lock:
            self._cleanup()
            if token in self.sessions:
                return token
        # Warm back into the in-memory cache; keep original token intact.
        new_token = self.upload_zip((ZIP_ROOT / (token + '.zip')).read_bytes())['token']
        with self.lock:
            self.sessions[token] = self.sessions.pop(new_token)
            self._cleanup()
        (ZIP_ROOT / (new_token + '.zip')).unlink(missing_ok=True)
        return token

    def get(self, token: str, index: int):
        if not isinstance(token,str) or not ZIP_TOKEN_RE.fullmatch(token):
            return None
        if token not in self.sessions and (ZIP_ROOT / (token + '.zip')).exists():
            self.restore_token(token)
        with self.lock:
            self._cleanup()
            data = self.sessions.get(token)
            if data is None or not (0 <= index < len(data['images'])):
                return None
            return data['images'][index]


def natural_sort_key(name: str):
    import re
    return tuple(int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', name))


STORE = MediaStore()
