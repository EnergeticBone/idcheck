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
    parser.add_argument("seq", help="Tên sequence (ví dụ: MOT20-01) hoặc đường dẫn file .zip / .xml")
    parser.add_argument("n", type=int, nargs="?", default=3, help="Số lượng lỗi chèn cho mỗi loại (mặc định: 3)")
    parser.add_argument("--seed", type=int, default=42, help="Seed ngẫu nhiên (mặc định: 42)")
    parser.add_argument("--fps", type=float, default=None, help="FPS của video (mặc định 5.0 cho BDD100K hoặc 30.0)")
    parser.add_argument("--out-dir", default=None, help="Thư mục xuất file (mặc định cùng thư mục với file nguồn hoặc data/injected)")
    parser.add_argument("--zero-indexed", action="store_true", default=True,
                        help="Dịch frame về 0-indexed (frame-1) cho MOT (mặc định: True)")
    parser.add_argument("--keep-frame-idx", action="store_true",
                        help="Giữ nguyên số frame (1-indexed như trong MOT)")
    parser.add_argument("--include-class-flip", action="store_true", default=False,
                        help="Chèn thêm lỗi đổi nhãn (class_flip) (mặc định: Không chèn)")
    args = parser.parse_args()

    seq = args.seq
    n = args.n
    seed = args.seed
    zero_indexed = not args.keep_frame_idx
    kinds = ["fragment", "gap", "jump", "switch", "duplicate"]
    if args.include_class_flip:
        kinds.append("class_flip")

    import zipfile
    from adapters.cvat_xml import load_cvat_xml, load_cvat_xml_meta

    is_file_input = os.path.isfile(seq) and (seq.endswith(".zip") or seq.endswith(".xml"))

    if is_file_input:
        in_path = pathlib.Path(seq)
        base_name = in_path.stem.replace(".zip", "").replace(".xml", "")
        if in_path.suffix == ".zip":
            with zipfile.ZipFile(in_path) as z:
                xml_names = [name for name in z.namelist() if name.endswith(".xml")]
                if not xml_names:
                    print(f"❌ Không tìm thấy file .xml nào trong zip '{seq}'")
                    sys.exit(1)
                xml_content = z.read(xml_names[0]).decode("utf-8")
        else:
            xml_content = in_path.read_text(encoding="utf-8")

        meta = load_cvat_xml_meta(xml_content)
        clean = load_cvat_xml(xml_content, interpolate=True)
        w = meta.get("width") or 1280
        h = meta.get("height") or 720
        size = meta.get("size") or (max(max(tr.boxes) for tr in clean.values()) + 1 if clean else 0)

        fps = args.fps or (5.0 if (w == 1280 and h == 720) else 30.0)
        k = fps / 30.0
        min_len = max(8, round(12 * k)) if k < 1.0 else 25
        margin = max(2, round(4 * k)) if k < 1.0 else 8

        dirty, truths = inject_many(
            clean,
            n_per_type=n,
            seed=seed,
            min_len=min_len,
            margin=margin,
            drop_range=(max(2, round(2 * k)), max(3, round(5 * k) if k < 1.0 else round(25 * k))),
            gap_range=(max(2, round(2 * k)), max(3, round(4 * k) if k < 1.0 else round(12 * k))),
            jump_range=(1.5, 4.0),
            kinds=kinds,
        )

        out_dir = pathlib.Path(args.out_dir) if args.out_dir else in_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1. Xuất file CVAT XML
        injected_xml_name = f"{base_name}_injected_cvat.xml"
        xml_path = out_dir / injected_xml_name
        meta_out = {
            "name": base_name,
            "size": size,
            "width": w,
            "height": h,
        }
        dump_cvat_xml(dirty, str(xml_path), meta=meta_out, zero_indexed=False)

        # 2. Nén thành zip với tên (tên zip cũ)_injected.zip chứa annotations.xml bên trong
        zip_out_path = out_dir / f"{base_name}_injected.zip"
        with zipfile.ZipFile(zip_out_path, "w", compression=zipfile.ZIP_DEFLATED) as z_out:
            z_out.write(xml_path, arcname="annotations.xml")

        # 3. Xuất file truth json
        truth_path = out_dir / f"{base_name}.truth.json"
        truth_path.write_text(json.dumps(truths, indent=2, ensure_ascii=False))

        counts = dict(Counter(t["type"] for t in truths))
        print("\n" + "=" * 60)
        print(f"✅ ĐÃ TẠO DỮ LIỆU LỖI GIẢ LẬP CHO: {base_name}")
        print("=" * 60)
        print(f"  - Track gốc sạch : {len(clean)} tracks")
        print(f"  - Track sau chèn : {len(dirty)} tracks")
        print(f"  - Tổng số lỗi    : {len(truths)} lỗi")
        print(f"  - Thống kê lỗi   : {counts}")
        print("\n📁 CÁC FILE ĐÃ XUẤT RA:")
        print(f"  1. File ZIP chuẩn CVAT : {zip_out_path}")
        print(f"     -> Chứa 'annotations.xml', upload trực tiếp lên CVAT")
        print(f"  2. File XML chi tiết   : {xml_path}")
        print(f"  3. File Truth JSON     : {truth_path}")
        print("=" * 60 + "\n")
        return

    # Tìm file gt.txt gốc trong data/clean (cho sequence MOT truyền thống)
    matches = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)
    if not matches:
        print(f"❌ Không tìm thấy dữ liệu gốc cho sequence hoặc file '{seq}'")
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

    fps = args.fps or 30.0
    try:
        if args.fps is None:
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
        kinds=kinds,
    )

    out_dir = pathlib.Path(args.out_dir) if args.out_dir else pathlib.Path("data/injected")
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

    # 2. Nén thành zip chứa annotations.xml
    zip_out_path = out_dir / f"{seq}_injected.zip"
    with zipfile.ZipFile(zip_out_path, "w", compression=zipfile.ZIP_DEFLATED) as z_out:
        z_out.write(xml_path, arcname="annotations.xml")

    # 3. Xuất file MOT format gt.txt
    mot_path = out_dir / f"{seq}_injected_gt.txt"
    save_mot_gt(dirty, str(mot_path))

    # 4. Xuất file truth json (để đối chiếu xem lỗi chèn ở đâu)
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
    print(f"\n📁 CÁC FILE ĐÃ XUẤT RA TẠI {out_dir}:")
    print(f"  1. File CVAT ZIP : {zip_out_path}")
    print(f"  2. File CVAT XML : {xml_path}")
    print(f"  3. File MOT GT   : {mot_path}")
    print(f"  4. File Truth JSON: {truth_path}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    import sys
    main()