import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import argparse
import glob
import json
import os
import pathlib
from collections import Counter

from adapters.mot import load_mot_gt, load_seqinfo, load_fps
from adapters.cvat_xml import dump_cvat_xml
from core.inject import inject_many


def save_mot_gt(tracks, out_path):
    """Xuất tracks ra file định dạng MOT gt.txt chuẩn."""
    rows = []
    class_map = {"pedestrian": 1, "person": 1, "car": 2, "truck": 3}
    for tid, tr in tracks.items():
        cid = class_map.get(tr.label, 1)
        for f in sorted(tr.boxes):
            b = tr.boxes[f]
            if b.outside:
                continue
            w = max(0.1, b.x2 - b.x1)
            h = max(0.1, b.y2 - b.y1)
            vis = 0.2 if b.occluded else 1.0
            rows.append((f, tid, b.x1, b.y1, w, h, 1, cid, vis))

    rows.sort(key=lambda r: (r[0], r[1]))
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(f"{r[0]},{r[1]},{r[2]:.2f},{r[3]:.2f},{r[4]:.2f},{r[5]:.2f},{r[6]},{r[7]},{r[8]:.2f}\n")


def main():
    parser = argparse.ArgumentParser(description="Tạo và xuất dữ liệu giả lập lỗi để import lên CVAT")
    parser.add_argument("seq", help="Tên sequence (ví dụ: MOT20-01, MOT17-09-FRCNN)")
    parser.add_argument("n", type=int, nargs="?", default=10, help="Số lượng lỗi chèn cho mỗi loại (mặc định: 10)")
    parser.add_argument("--seed", type=int, default=42, help="Seed ngẫu nhiên (mặc định: 42)")
    parser.add_argument("--zero-indexed", action="store_true", default=True,
                        help="Dịch frame về 0-indexed (frame-1) để khớp với video CVAT đếm từ frame 0 (mặc định: True)")
    parser.add_argument("--keep-frame-idx", action="store_true",
                        help="Giữ nguyên số frame (1-indexed như trong MOT)")
    args = parser.parse_args()

    seq = args.seq
    n = args.n
    seed = args.seed
    zero_indexed = not args.keep_frame_idx

    # Tìm file gt.txt gốc trong data/clean
    matches = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)
    if not matches:
        print(f"❌ Không tìm thấy dữ liệu gốc cho sequence '{seq}' trong data/clean/")
        sys.exit(1)

    gt_path = matches[0]
    clean = load_mot_gt(gt_path)

    # Đọc metadata kích thước ảnh và số frame nếu có
    w, h = 1920, 1080
    size = max(max(tr.boxes) for tr in clean.values()) if clean else 0
    try:
        w, h = load_seqinfo(gt_path)
    except Exception:
        pass

    fps = 30.0
    try:
        fps = load_fps(gt_path)
    except Exception:
        pass

    k = fps / 30.0
    dirty, truths = inject_many(
        clean,
        n_per_type=n,
        seed=seed,
        drop_range=(max(2, round(2 * k)), max(3, round(25 * k))),
        gap_range=(max(2, round(2 * k)), max(3, round(12 * k))),
        jump_range=(1.0, 4.0),
    )

    out_dir = pathlib.Path("data/injected")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Xuất file CVAT for video 1.1 XML (Khuyên dùng nhất cho CVAT)
    xml_path = out_dir / f"{seq}_injected_cvat.xml"
    meta = {
        "name": seq,
        "size": size,
        "width": w,
        "height": h,
    }
    dump_cvat_xml(dirty, str(xml_path), meta=meta, zero_indexed=zero_indexed)

    # 2. Xuất file MOT format gt.txt
    mot_path = out_dir / f"{seq}_injected_gt.txt"
    save_mot_gt(dirty, str(mot_path))

    # 3. Xuất file truth json (để đối chiếu xem lỗi chèn ở đâu)
    truth_path = out_dir / f"{seq}.truth.json"
    truth_path.write_text(json.dumps(truths, indent=2, ensure_ascii=False))

    counts = dict(Counter(t["type"] for t in truths))
    print("\n" + "=" * 60)
    print(f"✅ ĐÃ TẠO DỮ LIỆU LỖI GIẢ LẬP CHO: {seq}")
    print("=" * 60)
    print(f"  - Track gốc sạch : {len(clean)} tracks")
    print(f"  - Track sau chèn : {len(dirty)} tracks")
    print(f"  - Tổng số lỗi    : {len(truths)} lỗi")
    print(f"  - Thống kê lỗi   : {counts}")
    print("\n📁 CÁC FILE ĐÃ XUẤT RA TẠI data/injected/:")
    print(f"  1. File CVAT XML : {xml_path}")
    print(f"     -> Dùng để import trực tiếp vào CVAT (chọn format 'CVAT 1.1')")
    print(f"  2. File MOT GT   : {mot_path}")
    print(f"     -> Dùng nếu bạn import dạng MOT format")
    print(f"  3. File Truth JSON: {truth_path}")
    print(f"     -> Chứa danh sách chi tiết từng lỗi chèn ở frame nào để đối chiếu")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    import sys
    main()