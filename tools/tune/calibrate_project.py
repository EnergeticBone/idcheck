"""Công cụ tự động hiệu chuẩn (auto-calibration) ngưỡng phát hiện lỗi từ các Job Ground Truth trên CVAT.

Quy trình:
1. Tải toàn bộ track từ các Job mẫu trên CVAT (mặc định lọc stage='acceptance').
2. Tạo tập kiểm thử bằng cách chèn lỗi nhân tạo (jump, switch, gap, merge) qua inject_many.
3. Chạy grid-search để tìm bộ ngưỡng tối ưu (F1-score cao nhất, ít báo oan nhất trên dữ liệu sạch).
4. Xuất file cấu hình riêng cho project: config_project_<id>.yaml.
"""
import copy
import logging
import os
import sys
import yaml
from collections import Counter

# Đảm bảo import được các module trong idcheck
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from adapters.cvat_api import get_client, load_job_tracks
from core.config import resolve_cfg
from core.evaluate import evaluate_merge
from core.inject import inject_many
from core.rules import check_gaps, check_jumps, check_switches

logger = logging.getLogger("idcheck.calibrate")


def collect_project_jobs(client, project_id, stage_filter="acceptance"):
    """Lấy danh sách job_id của project.

    Nếu stage_filter được chỉ định, chỉ lấy các job có stage khớp.
    """
    project = client.projects.retrieve(project_id)
    tasks = project.get_tasks()
    selected_jobs = []

    for task in tasks:
        for job in task.get_jobs():
            if stage_filter is None or getattr(job, "stage", None) == stage_filter:
                selected_jobs.append(job.id)

    return selected_jobs


def tune_max_speed(clean_data, dirty_data, base_cfg, fps, speeds=(8.0, 10.0, 12.0, 15.0, 18.0, 20.0, 25.0)):
    """Tìm max_speed (cỡ box / giây) tối ưu:

    Ưu tiên FP trên tập sạch cực thấp (không báo oan), sau đó tối đa hóa Recall trên tập lỗi.
    """
    best_speed = 15.0
    best_score = -1.0

    logger.info("--- [1/3] Bắt đầu hiệu chỉnh max_speed (Jump Threshold) ---")

    for sp in speeds:
        cfg = copy.deepcopy(base_cfg)
        cfg["default"]["max_speed"] = sp
        r_cfg = resolve_cfg(cfg, fps)

        total_fp = 0
        total_hit = 0
        total_want = 0

        for jid, clean in clean_data.items():
            dirty, truths = dirty_data[jid]
            want = {(t["a"], t["frame"]) for t in truths if t["type"] == "jump"}

            # Đếm số cảnh báo trên tập sạch (False Positives)
            fp = sum(len(check_jumps(tr, r_cfg)) for tr in clean.values())
            # Đếm số cảnh báo bắt trúng trên tập lỗi (True Positives)
            got = {(e["track_id"], e["frame"]) for tr in dirty.values() for e in check_jumps(tr, r_cfg)}

            total_fp += fp
            total_want += len(want)
            total_hit += len(want & got)

        recall = total_hit / max(total_want, 1)
        # Điểm phạt nặng cho False Positive trên tập sạch
        score = recall - (total_fp * 0.05)

        logger.info("  max_speed=%4.1f box/s | Cảnh báo sạch (FP)=%d | Recall jump=%d/%d (%.1f%%)",
                    sp, total_fp, total_hit, total_want, recall * 100)

        if score > best_score:
            best_score = score
            best_speed = sp

    logger.info("=> Chọn max_speed tối ưu: %.1f cỡ box/giây", best_speed)
    return best_speed


def tune_switch_params(clean_data, dirty_data, base_cfg, fps,
                       seps=(0.05, 0.1, 0.15, 0.2, 0.3),
                       ratios=(0.4, 0.5, 0.6)):
    """Tìm swap_min_sep và swap_cross_ratio tối ưu cho hoán ID."""
    best_sep = 0.1
    best_ratio = 0.5
    best_f1 = -1.0

    logger.info("--- [2/3] Bắt đầu hiệu chỉnh Switch Thresholds ---")

    for sep in seps:
        for rat in ratios:
            cfg = copy.deepcopy(base_cfg)
            cfg["default"]["swap_min_sep"] = sep
            cfg["default"]["swap_cross_ratio"] = rat
            r_cfg = resolve_cfg(cfg, fps)

            tp, fp, fn = 0, 0, 0

            for jid, clean in clean_data.items():
                dirty, truths = dirty_data[jid]
                want = {tuple(sorted((t["a"], t["b"]))): t["frame"]
                        for t in truths if t["type"] == "switch" and t["b"] is not None}

                # Cảnh báo trên tập sạch (FP)
                fp += len(check_switches(clean, r_cfg))

                # Đánh giá trên tập bẩn
                alerts = check_switches(dirty, r_cfg)
                got = {tuple(sorted((e["track_id"], e["other_id"]))): e["frame"]
                       for e in alerts if "other_id" in e}

                for pair, f in want.items():
                    if pair in got and abs(got[pair] - f) <= 2:
                        tp += 1
                    else:
                        fn += 1

            precision = tp / max(tp + fp, 1)
            recall = tp / max(tp + fn, 1)
            f1 = 2 * precision * recall / max(precision + recall, 1e-6)

            if f1 > best_f1:
                best_f1 = f1
                best_sep = sep
                best_ratio = rat

    logger.info("=> Chọn switch tối ưu: swap_min_sep=%.2f, swap_cross_ratio=%.2f (F1: %.3f)",
                best_sep, best_ratio, best_f1)
    return best_sep, best_ratio


def tune_merge_params(clean_data, dirty_data, base_cfg, fps,
                      costs=(1.0, 1.4, 1.6, 2.0, 2.5),
                      gap_s_list=(0.8, 1.0, 1.2, 1.5)):
    """Tìm merge_max_cost và max_merge_gap_s tối ưu để nối các track đứt."""
    best_cost = 1.6
    best_gap_s = 1.0
    best_f1 = -1.0

    logger.info("--- [3/3] Bắt đầu hiệu chỉnh Merge Thresholds ---")

    # Giả định kích thước khung hình chuẩn của BDD100k là 1280x720
    frame_size = (1280, 720)

    for cost in costs:
        for gap_s in gap_s_list:
            cfg = copy.deepcopy(base_cfg)
            cfg["default"]["merge_max_cost"] = cost
            cfg["default"]["max_merge_gap_s"] = gap_s
            r_cfg = resolve_cfg(cfg, fps)

            tot_truth = 0
            tot_tp = 0
            tot_fp = 0

            for jid, clean in clean_data.items():
                dirty, truths = dirty_data[jid]
                res = evaluate_merge(clean, dirty, truths, r_cfg, thresholds=(0.5,), frame_size=frame_size)
                r = res[0.5]
                tot_truth += r["truth"]
                tot_tp += r["tp"]
                tot_fp += r["fp"]

            prec = tot_tp / max(tot_tp + tot_fp, 1)
            rec = tot_tp / max(tot_truth, 1)
            f1 = 2 * prec * rec / max(prec + rec, 1e-6)

            if f1 > best_f1:
                best_f1 = f1
                best_cost = cost
                best_gap_s = gap_s

    logger.info("=> Chọn merge tối ưu: merge_max_cost=%.1f, max_merge_gap_s=%.1fs (F1: %.3f)",
                best_cost, best_gap_s, best_f1)
    return best_cost, best_gap_s


def calibrate_project(client, project_id, job_ids=None, fps=5.0,
                      base_config_path="config_base.yaml", out_config_path=None):
    """Quy trình hiệu chuẩn hoàn chỉnh cho 1 project CVAT."""
    if not job_ids:
        job_ids = collect_project_jobs(client, project_id, stage_filter="acceptance")
        if not job_ids:
            logger.warning("Không tìm thấy Job nào ở stage 'acceptance' trong Project #%s.", project_id)
            return None

    logger.info("Bắt đầu calibrate Project #%s với %d Job mẫu: %s", project_id, len(job_ids), job_ids)

    # 1. Tải tracks từ CVAT
    clean_data = {}
    for jid in job_ids:
        logger.info("Đang tải dữ liệu Job #%d từ CVAT...", jid)
        tracks, _ = load_job_tracks(client, jid, interpolate=True)
        if tracks:
            clean_data[jid] = tracks
        else:
            logger.warning("Job #%d không có track nào, bỏ qua.", jid)

    if not clean_data:
        raise ValueError("Không tải được track nào từ danh sách Job đã chọn.")

    total_tracks = sum(len(tr) for tr in clean_data.values())
    logger.info("Đã tải tổng cộng %d track từ %d Job mẫu.", total_tracks, len(clean_data))

    # 2. Sinh tập dữ liệu lỗi tổng hợp (dirty_data)
    dirty_data = {}
    k = fps / 30.0
    for jid, clean in clean_data.items():
        dirty, truths = inject_many(
            clean,
            n_per_type=8,
            seed=jid,
            drop_range=(max(1, round(2 * k)), max(2, round(8 * k))),
            gap_range=(max(1, round(2 * k)), max(2, round(6 * k))),
            jump_range=(1.5, 4.0),
        )
        dirty_data[jid] = (dirty, truths)

    # 3. Đọc cấu hình nền (base_config)
    with open(base_config_path, "r", encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f) or {}

    # 4. Chạy Tuning từng nhóm quy luật
    opt_speed = tune_max_speed(clean_data, dirty_data, base_cfg, fps)
    opt_sep, opt_ratio = tune_switch_params(clean_data, dirty_data, base_cfg, fps)
    opt_cost, opt_gap_s = tune_merge_params(clean_data, dirty_data, base_cfg, fps)

    # 5. Tạo cấu hình mới đã được tối ưu
    tuned_cfg = copy.deepcopy(base_cfg)
    tuned_cfg.setdefault("default", {})
    tuned_cfg["default"]["max_speed"] = opt_speed
    tuned_cfg["default"]["swap_min_sep"] = opt_sep
    tuned_cfg["default"]["swap_cross_ratio"] = opt_ratio
    tuned_cfg["default"]["merge_max_cost"] = opt_cost
    tuned_cfg["default"]["max_merge_gap_s"] = opt_gap_s

    # Đồng bộ thông số xe trong classes nếu có
    for cls_name in ("car", "truck", "other vehicle"):
        tuned_cfg.setdefault("classes", {}).setdefault(cls_name, {})
        tuned_cfg["classes"][cls_name]["max_speed"] = opt_speed

    if not out_config_path:
        out_config_path = f"config_project_{project_id}.yaml"

    with open(out_config_path, "w", encoding="utf-8") as f:
        yaml.dump(tuned_cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    logger.info("=== HIỆU CHUẨN HOÀN TẤT! ===")
    logger.info("Đã lưu cấu hình tối ưu vào: %s", out_config_path)
    return out_config_path


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Auto-calibrate project thresholds from CVAT Ground Truth jobs")
    parser.add_argument("--project", type=int, required=True, help="Project ID trên CVAT")
    parser.add_argument("--jobs", type=int, nargs="+", default=None, help="Danh sách Job ID chỉ định (nếu bỏ trống sẽ lấy các job acceptance)")
    parser.add_argument("--fps", type=float, default=5.0, help="FPS của video (mặc định 5.0 cho BDD100K)")
    parser.add_argument("-c", "--base-config", default="config_base.yaml", help="File cấu hình nền")
    parser.add_argument("-o", "--out", default=None, help="File cấu hình đầu ra")
    parser.add_argument("--host", default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    args = parser.parse_args()

    client = get_client(host=args.host, user=args.user, password=args.password)
    calibrate_project(
        client=client,
        project_id=args.project,
        job_ids=args.jobs,
        fps=args.fps,
        base_config_path=args.base_config,
        out_config_path=args.out,
    )
