"""Giải thích vì sao cặp track A -> B có / không được gợi ý nối.
  python explain_pair.py mot  <gt.txt>    <fps> <config.yaml> --a ID --b ID [--no-scale] [--min-score 0.5]
  python explain_pair.py cvat <task_id>   <fps> <config.yaml> --a ID --b ID
  python explain_pair.py bdd  <file.json> <fps> <config.yaml> --a ID --b ID   (cần adapters.bdd.load_bdd_video)
"""
import os, sys, argparse, yaml
from math import hypot, exp
from core.config import resolve_cfg
from core.rules import center, size
from core.merge import predict, suggest_merges, _p, _border_sides


def vis_frames(tr):
    return sorted(f for f, b in tr.boxes.items() if not b.outside)


def explain(tracks, a, b, cfg, fsz=None, min_score=0.5, out=print):
    A, B = tracks[a], tracks[b]
    P = _p(cfg, A.label)
    fa, fb = vis_frames(A), vis_frames(B)
    out("== 1. Dữ liệu của hai track ==")
    for name, tr, fr in (("A", A, fa), ("B", B, fb)):
        n_out = sum(1 for x in tr.boxes.values() if x.outside)
        out(f"  {name}={tr.id}: nhãn '{tr.label}', {len(tr.boxes)} box ({n_out} box outside), "
            f"frame nhìn thấy {fr[0]}..{fr[-1]}, Track.start={tr.start}, Track.end={tr.end}")
    warn = []
    if B.start != fb[0]:
        warn.append(f"B có box outside TRƯỚC khi xuất hiện: Track.start={B.start} nhưng frame đầu nhìn thấy={fb[0]}. "
                    f"Merge đang tính gap từ frame {B.start} (sai). Sửa Track.start (xem hướng dẫn).")
    if A.end != fa[-1]:
        warn.append(f"Track.end={A.end} khác frame cuối nhìn thấy của A={fa[-1]}")
    for w in warn:
        out("  !! " + w)
    out("  3 box cuối nhìn thấy của A:", [(f, round(A.boxes[f].x1), round(A.boxes[f].y1), round(A.boxes[f].x2), round(A.boxes[f].y2)) for f in fa[-3:]])
    out("  3 box đầu nhìn thấy của B:", [(f, round(B.boxes[f].x1), round(B.boxes[f].y1), round(B.boxes[f].x2), round(B.boxes[f].y2)) for f in fb[:3]])

    out("\n== 2. Từng bước lọc của suggest_merges (dùng ngưỡng đã co giãn theo FPS nếu có) ==")
    out(f"  ngưỡng hiệu dụng: max_merge_gap={P['max_merge_gap']}  max_merge_dist={P['max_merge_dist']}  "
        f"max_merge_scale={P['max_merge_scale']}  merge_max_cost={P['merge_max_cost']}  border_margin={P['border_margin']}")
    stop = None
    ok = A.label == B.label
    out(f"  [{'đạt' if ok else 'LOẠI'}] cùng nhãn: '{A.label}' và '{B.label}'")
    stop = stop or (None if ok else "nhãn khác nhau")
    gap = B.start - A.end
    ok = 1 <= gap <= P["max_merge_gap"]
    out(f"  [{'đạt' if ok else 'LOẠI'}] khoảng trống B.start - A.end = {B.start} - {A.end} = {gap} (cần 1..{P['max_merge_gap']})")
    stop = stop or (None if ok else f"gap {gap} ngoài 1..{P['max_merge_gap']}")
    end_box, b_start = A.boxes[A.end], B.boxes[B.start]
    ratio = size(b_start) / size(end_box)
    ok = 1 / P["max_merge_scale"] < ratio < P["max_merge_scale"]
    out(f"  [{'đạt' if ok else 'LOẠI'}] tỉ lệ cỡ box B/A = {ratio:.2f} (cần trong {1 / P['max_merge_scale']:.2f}..{P['max_merge_scale']})")
    stop = stop or (None if ok else "tỉ lệ cỡ box")
    if gap >= 1:
        px, py = predict(A, gap)
        bx, by = center(b_start)
        dist = hypot(px - bx, py - by) / size(end_box)
        ok = dist < P["max_merge_dist"]
        out(f"  [{'đạt' if ok else 'LOẠI'}] vị trí dự đoán của A sau {gap} frame: ({px:.0f},{py:.0f}); B bắt đầu tại ({bx:.0f},{by:.0f}); "
            f"dist = {dist:.2f} cỡ box (cần < {P['max_merge_dist']})")
        stop = stop or (None if ok else f"dist {dist:.2f} quá lớn")
        geo = dist
        if fsz is not None and P["border_margin"] > 0:
            both = _border_sides(end_box, fsz, P["border_margin"]) & _border_sides(b_start, fsz, P["border_margin"])
            if both:
                geo += P["border_penalty"]
                out(f"  [phạt] A kết thúc và B bắt đầu cùng chạm mép {sorted(both)}: cộng {P['border_penalty']}")
        elif P["border_margin"] > 0:
            out("  [bỏ qua] không có kích thước ảnh nên không xét phạt mép khung")
        cost = geo + P.get("merge_gap_cost", 0.05) * gap
        score = exp(-geo)
        out(f"  cost = {cost:.2f}; score = exp(-{geo:.2f}) = {score:.3f}")
        ok = score >= min_score
        out(f"  [{'đạt' if ok else 'LOẠI'}] score {score:.3f} so với min_score {min_score}")
        stop = stop or (None if ok else f"score {score:.3f} < min_score {min_score} (dist cao)")
        out(f"  (ghép cặp chỉ được chọn nếu cost < {P['merge_max_cost']} và không có đối thủ rẻ hơn)")
        if cost >= P["merge_max_cost"]:
            stop = stop or f"cost {cost:.2f} >= merge_max_cost"

    out("\n== 3. Kết quả thật của suggest_merges (min_score=0) liên quan tới A hoặc B ==")
    res = suggest_merges(tracks, cfg, min_score=0.0, frame_size=fsz)
    rel = [m for m in res if m["a"] == a or m["b"] == b]
    for m in rel:
        tag = "<-- CẶP CẦN XEM" if (m["a"], m["b"]) == (a, b) else "đối thủ"
        out(f"  {m['a']} -> {m['b']} score {m['score']} gap {m['gap']}  {tag}")
    if not rel:
        out("  (không có gợi ý nào)")
    found = [m for m in rel if (m["a"], m["b"]) == (a, b)]
    out("\n== KẾT LUẬN ==")
    if found and found[0]["score"] >= min_score:
        out(f"  Cặp A->B CÓ được gợi ý (score {found[0]['score']}). Nếu không thấy trên giao diện, lỗi nằm ở bước hiển thị / lọc của chương trình gọi.")
    elif found:
        out(f"  Cặp có trong kết quả nhưng score {found[0]['score']} < min_score {min_score}.")
    elif stop:
        out("  Cặp bị loại ở bước đầu tiên không đạt: " + stop)
    else:
        out("  Cặp qua mọi bước lọc nhưng bị đối thủ thắng trong ghép cặp một-một (xem mục 3).")
    return stop


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source"); ap.add_argument("path"); ap.add_argument("fps", type=float); ap.add_argument("config")
    ap.add_argument("--a", type=int, required=True); ap.add_argument("--b", type=int, required=True)
    ap.add_argument("--no-scale", action="store_true"); ap.add_argument("--min-score", type=float, default=0.5)
    args = ap.parse_args()
    fsz = None
    if args.source == "mot":
        from adapters.mot import load_mot_gt, load_seqinfo
        tracks = load_mot_gt(args.path)
        try:
            fsz = load_seqinfo(args.path)
        except Exception:
            pass
    elif args.source == "cvat":
        from cvat_loader import load_cvat
        tracks = load_cvat(args.path)
    else:
        from adapters.bdd import load_bdd_video
        tracks = load_bdd_video(args.path)
    cfg = yaml.safe_load(open(args.config))
    cfg = resolve_cfg(cfg, args.fps)
    explain(tracks, args.a, args.b, cfg, fsz, args.min_score)