import sys, glob, yaml
from math import hypot, log
from adapters.mot import load_mot_gt, load_seqinfo, load_fps
from core.config import scale_cfg
from core.inject import inject_many
from core.rules import center, size
from core.merge import predict, suggest_merges, _p, _border_sides

seq = sys.argv[1]
cfg = yaml.safe_load(open(sys.argv[2]))
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 0
gt = glob.glob(f"data/clean/**/{seq}/gt/gt.txt", recursive=True)[0]
fps = load_fps(gt)
cfg = scale_cfg(cfg, fps)
fsz = load_seqinfo(gt)
clean = load_mot_gt(gt)
k = fps / 30.0
dirty, truths = inject_many(clean, n_per_type=10, seed=seed,
                            drop_range=(max(2, round(2 * k)), max(3, round(25 * k))),
                            gap_range=(max(2, round(2 * k)), max(3, round(12 * k))),
                            jump_range=(1.0, 4.0))
T = {(t["a"], t["b"]) for t in truths if t["type"] == "fragment"}
base = {(m["a"], m["b"]) for m in suggest_merges(clean, cfg, frame_size=fsz)}
sugg = suggest_merges(dirty, cfg, frame_size=fsz)
got = {(m["a"], m["b"]) for m in sugg}


def tag(k):
    return "ĐÚNG (fragment khác đã chèn)" if k in T else (
        "có sẵn trên dữ liệu sạch" if k in base else "gợi ý khác")


print(f"{seq} seed {seed}: fragment đã chèn {len(T)}, tìm lại được {len(T & got)}")
for a, b in sorted(T - got):
    A, B = dirty[a], dirty[b]
    p = _p(cfg, A.label)
    gap = B.start - A.end
    e, s = A.boxes[A.end], B.boxes[B.start]
    ratio = size(s) / size(e)
    px, py = predict(A, gap)
    dist = hypot(px - center(s)[0], py - center(s)[1]) / size(e)
    mg = p.get("border_margin", 0)
    border = bool(_border_sides(e, fsz, mg) & _border_sides(s, fsz, mg))
    cost = dist + 0.05 * gap + (p.get("border_penalty", 2.0) if border else 0)
    print(f"\nSÓT {a}->{b}: gap {gap}, dist {dist:.2f}, tỉ lệ cỡ {ratio:.2f}, "
          f"chạm mép {border}, cost {cost:.2f}")
    rivals = [m for m in sugg if m["a"] == a or m["b"] == b]
    if not rivals:
        print("    không có đối thủ (bị loại do ngưỡng lọc ứng viên)")
    for m in rivals:
        c = -log(max(m["score"], 1e-3)) + 0.05 * m["gap"]
        side = "A đã nối sang" if m["a"] == a else "B đã được nối từ"
        other = m["b"] if m["a"] == a else m["a"]
        print(f"    {side} {other}: gap {m['gap']}, score {m['score']}, "
              f"cost {c:.2f} | {tag((m['a'], m['b']))}")
