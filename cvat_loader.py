"""Nạp track từ CVAT bằng hàm có sẵn trong adapters/cvat_api.py, tự dò tên hàm và cách gọi."""
import os, sys, inspect


def _as_tracks(res):
    tracks = res[0] if isinstance(res, tuple) else res
    if isinstance(tracks, list):
        tracks = {t.id: t for t in tracks}
    if not isinstance(tracks, dict) or not tracks:
        return None
    first = next(iter(tracks.values()))
    return tracks if hasattr(first, "boxes") else None


def load_cvat(item_id):
    from adapters import cvat_api
    item_id = int(item_id)
    client = cvat_api.get_client()          # đọc biến môi trường CVAT_*
    with client:
        tracks = _as_tracks(cvat_api.load_job_tracks(
            client, item_id, interpolate=True, use_client_id=True))
    if not tracks:
        sys.exit(f"Job {item_id} không có track nào (hoặc kết quả không phải dict[id, Track])")
    print(f"[nạp CVAT] OK: {len(tracks)} track từ job {item_id}")
    return tracks