"""Persistent local video ingestion and exact decoded frame access via FFmpeg.

This avoids browser codec incompatibility and replaces imprecise seek events.
The frame index is the source video frame index (0-based); sampled tasks can
specify source start/step independently in the web UI.
"""
import json
import mimetypes
import re
import secrets
import subprocess
import shutil
import threading
from collections import OrderedDict
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'storage' / 'video_cache'
MAX_VIDEO_BYTES = 300 * 1024 * 1024
EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{16,64}$')

class VideoStore:
    def __init__(self):
        self.cache = OrderedDict()
        self.lock = threading.RLock()
        self.cache_limit = 128
        self._last_cleanup = 0

    def metadata(self, token):
        if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
            raise ValueError('Token video không hợp lệ')
        file = ROOT / (token + '.json')
        if not file.exists():
            raise ValueError('Không tìm thấy video; hãy tải video lại.')
        meta = json.loads(file.read_text(encoding='utf-8'))
        if not (ROOT / meta['filename']).is_file():
            raise ValueError('File video không còn trên đĩa')
        return meta

    def add(self, stream, size, filename):
        suffix = Path(filename).suffix.lower()
        if suffix not in EXTENSIONS:
            raise ValueError('Định dạng video: MP4, MOV, AVI, MKV, WEBM hoặc M4V')
        if size <= 0 or size > MAX_VIDEO_BYTES:
            raise ValueError('Video rỗng hoặc vượt 300 MB')
        ROOT.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(20)
        target = ROOT / (token + suffix)
        try:
            with target.open('wb') as f:
                remaining = size
                while remaining:
                    part = stream.read(min(1024 * 1024, remaining))
                    if not part:
                        raise ValueError('Video upload chưa đầy đủ')
                    f.write(part)
                    remaining -= len(part)
            info = self._probe(target)
            # Fail at upload time rather than showing a broken preview after success.
            first_frame = self._decode_path(target, 0, timeout=35)
            info.update(token=token, filename=target.name, original_name=Path(filename).name, bytes=size)
            (ROOT / (token + '.json')).write_text(json.dumps(info), encoding='utf-8')
            with self.lock:
                self.cache[(token, 0)] = first_frame
            return info
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def _probe(self, filepath):
        try:
            proc = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(filepath)],
                                  capture_output=True,text=True,timeout=25)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise ValueError('Cần cài ffmpeg và ffprobe: sudo apt install ffmpeg') from e
        if proc.returncode:
            raise ValueError('Không đọc được codec/video. Kiểm tra file hoặc chuyển sang MP4 H.264.')
        payload = json.loads(proc.stdout)
        track = next((s for s in payload.get('streams',[]) if s.get('codec_type')=='video'),None)
        if not track or not track.get('width') or not track.get('height'):
            raise ValueError('File không chứa video hợp lệ')
        fps = 0
        for rate in [track.get('avg_frame_rate'),track.get('r_frame_rate')]:
            try:
                fps=float(Fraction(rate))
                if fps > 0:break
            except (TypeError,ValueError,ZeroDivisionError):pass
        fps = round(fps,5) if 0 < fps <= 240 else 30
        duration=float(track.get('duration') or payload.get('format',{}).get('duration') or 0)
        frames=int(track.get('nb_frames') or 0)
        if not frames and duration:frames=int(round(duration*fps))
        return dict(width=int(track['width']),height=int(track['height']),fps=fps,
                    duration=round(duration,3),frame_count=frames,codec=track.get('codec_name','unknown'))

    @staticmethod
    def diagnostics():
        result = {}
        for exe in ('ffmpeg', 'ffprobe'):
            path = shutil.which(exe)
            if not path:
                result[exe] = {'installed': False, 'version': None}
                continue
            try:
                output = subprocess.run([exe, '-version'], capture_output=True, text=True, timeout=5)
                result[exe] = {'installed': output.returncode == 0,
                               'version': output.stdout.splitlines()[0][:180] if output.stdout else None}
            except (OSError, subprocess.TimeoutExpired):
                result[exe] = {'installed': False, 'version': None}
        return result

    @staticmethod
    def _decode_path(filepath, index, timeout=35):
        # `-fps_mode` does not exist in older Ubuntu FFmpeg builds (e.g. 4.4).
        # `-vsync 0` is compatible with both older Ubuntu and recent FFmpeg.
        cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
               '-i', str(filepath), '-map', '0:v:0',
               '-vf', f'select=eq(n\\,{index})', '-vsync', '0',
               '-frames:v', '1', '-q:v', '3', '-vcodec', 'mjpeg',
               '-f', 'image2pipe', 'pipe:1']
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        except FileNotFoundError as e:
            raise ValueError('Không tìm thấy FFmpeg. Ubuntu: sudo apt update && sudo apt install ffmpeg') from e
        except subprocess.TimeoutExpired as e:
            raise ValueError(f'Giải mã frame {index} quá {timeout}s. Video dài / codec nặng; '
                             'hãy dùng frames.zip hoặc video đã chuyển sang H.264.') from e
        if proc.returncode or not proc.stdout.startswith(b'\xff\xd8\xff'):
            stderr = proc.stderr.decode('utf-8', 'replace').strip()
            tail = ' | '.join(stderr.splitlines()[-3:])[:480]
            if 'Unrecognized option' in stderr or 'Option not found' in stderr:
                guidance = 'Phiên bản FFmpeg không hỗ trợ một tùy chọn giải mã.'
            elif 'Decoder' in stderr or 'Unsupported' in stderr or 'Unknown decoder' in stderr:
                guidance = 'Codec video không được bản FFmpeg này hỗ trợ.'
            elif 'Invalid data' in stderr or 'moov atom not found' in stderr:
                guidance = 'Tệp video không hợp lệ hoặc upload chưa đầy đủ.'
            else:
                guidance = f'FFmpeg không tạo được ảnh cho frame {index}.'
            raise ValueError(f'{guidance} {tail}'.strip())
        return proc.stdout

    def frame(self, token, index):
        meta = self.metadata(token)
        if not isinstance(index, int) or not 0 <= index <= 200000:
            raise ValueError('Frame video không hợp lệ')
        if meta.get('frame_count') and index >= meta['frame_count']:
            raise ValueError(f'Frame {index} nằm ngoài video ({meta["frame_count"]} frame)')
        key = (token, index)

        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]

        # 1. Kiểm tra cache frame trên đĩa (disk-backed frame cache)
        frame_dir = ROOT / f"{token}_frames"
        frame_file = frame_dir / f"{index}.jpg"
        if frame_file.is_file():
            try:
                data = frame_file.read_bytes()
                with self.lock:
                    self.cache[key] = data
                    self.cache.move_to_end(key)
                    while len(self.cache) > self.cache_limit:
                        self.cache.popitem(last=False)
                return data
            except OSError:
                pass

        # 2. Giải mã bằng FFmpeg
        data = self._decode_path(ROOT / meta['filename'], index)

        # 3. Lưu cache frame vào đĩa để tái sử dụng ngay lập tức
        try:
            frame_dir.mkdir(parents=True, exist_ok=True)
            frame_file.write_bytes(data)
        except OSError:
            pass

        with self.lock:
            self.cache[key] = data
            self.cache.move_to_end(key)
            while len(self.cache) > self.cache_limit:
                self.cache.popitem(last=False)
        return data

    def cleanup(self, max_days=7):
        """Dọn dẹp các video và frame cache cũ hơn max_days."""
        import time
        now = time.time()
        cutoff = now - max_days * 86400
        if not ROOT.exists():
            return
        with self.lock:
            for json_file in ROOT.glob("*.json"):
                try:
                    if json_file.stat().st_mtime < cutoff:
                        token = json_file.stem
                        meta = json.loads(json_file.read_text(encoding='utf-8'))
                        video_file = ROOT / meta.get('filename', '')
                        if video_file.is_file():
                            video_file.unlink(missing_ok=True)
                        frame_dir = ROOT / f"{token}_frames"
                        if frame_dir.is_dir():
                            shutil.rmtree(frame_dir, ignore_errors=True)
                        json_file.unlink(missing_ok=True)
                except Exception:
                    pass

VIDEO = VideoStore()
