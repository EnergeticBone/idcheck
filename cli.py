import argparse
import json
import logging
import os
import sys
import threading
import yaml

from core.config import resolve_cfg
from core.rules import run_rules
from core.merge import suggest_merges
from adapters.cvat_xml import load_cvat_xml, load_cvat_xml_meta
from adapters.cvat_api import (
    get_client,
    check_job,
    load_job_tracks,
    post_alerts_as_issues,
    post_merges_as_issues,
)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("idcheck")


def load_config(config_path, fps=None):
    if not os.path.isfile(config_path):
        logger.warning("Không tìm thấy file cấu hình %s, sử dụng mặc định rỗng.", config_path)
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if fps is not None:
        return resolve_cfg(cfg, fps)
    has_seconds = any(k.endswith("_s") or k == "max_speed" for k in cfg.get("default", {}))
    if has_seconds:
        effective_fps = cfg.get("fps", 30.0)
        return resolve_cfg(cfg, effective_fps)
    return cfg


def print_report(alerts, merges=None, issue_results=None):
    print("\n" + "=" * 60)
    print("                 KẾT QUẢ KIỂM TRA IDCHECK")
    print("=" * 60)

    # Thống kê alerts theo loại
    counts = {}
    for a in alerts:
        t = a["type"].upper()
        counts[t] = counts.get(t, 0) + 1

    print("\n[1] Thống kê cảnh báo lỗi:")
    if not counts:
        print("  -> Không phát hiện lỗi nào! (Dữ liệu sạch)")
    else:
        for k, v in sorted(counts.items()):
            print(f"  - {k:<15}: {v} cảnh báo")
        print(f"  -> Tổng cộng     : {len(alerts)} cảnh báo")

    if alerts:
        print("\n[2] Chi tiết cảnh báo:")
        for idx, a in enumerate(alerts, 1):
            t = a["type"].upper()
            f = a["frame"]
            tid = a["track_id"]
            other = a.get("other_id")
            track_str = f"Track #{tid}" if other is None else f"Track #{tid} & #{other}"
            print(f"  {idx:2d}. [{t}] Frame {f:4d} | {track_str}: {a['detail']}")

    if merges is not None:
        print("\n[3] Gợi ý nối track (Merges):")
        if not merges:
            print("  -> Không có gợi ý nối track nào.")
        else:
            for idx, m in enumerate(merges, 1):
                print(f"  {idx:2d}. Nối Track #{m['a']} -> #{m['b']} | Gap: {m['gap']:2d} frames | Score: {m['score']:.3f}")

    if issue_results:
        print("\n[4] Kết quả tạo Issue trên CVAT:")
        created = sum(1 for r in issue_results if r.get("status") == "created")
        skipped = sum(1 for r in issue_results if r.get("status") == "skipped")
        dry_run = sum(1 for r in issue_results if r.get("status") == "dry_run")
        errors = sum(1 for r in issue_results if r.get("status") == "error")
        if dry_run:
            print(f"  -> [DRY-RUN] Dự kiến tạo {dry_run} issues.")
        else:
            print(f"  -> Đã tạo mới: {created} | Bỏ qua trùng: {skipped} | Lỗi: {errors}")

    print("=" * 60 + "\n")


def cmd_check_xml(args):
    cfg = load_config(args.config, args.fps)
    meta = load_cvat_xml_meta(args.xml_file)
    tracks = load_cvat_xml(args.xml_file, interpolate=not args.no_interpolate)

    frame_size = (meta["width"], meta["height"]) if meta.get("width") and meta.get("height") else None
    alerts = run_rules(tracks, cfg, frame_size=frame_size)
    merges = suggest_merges(tracks, cfg, min_score=args.min_score, frame_size=frame_size)

    if args.json:
        out = {
            "file": args.xml_file,
            "meta": meta,
            "num_tracks": len(tracks),
            "alerts": alerts,
            "merges": merges,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"Đã nạp file XML: {args.xml_file} ({len(tracks)} tracks)")
        if meta.get("width") and meta.get("height"):
            print(f"Kích thước video: {meta['width']}x{meta['height']}, số frame: {meta.get('size')}")
        print_report(alerts, merges)


def cmd_check_job(args):
    cfg = load_config(args.config, args.fps)
    client = get_client(host=args.host, user=args.user, password=args.password)

    res = check_job(
        client=client,
        job_id=args.job_id,
        cfg=cfg,
        create_issues=args.create_issues,
        dry_run=args.dry_run,
        include_merges=args.include_merges,
        min_score=args.min_score,
    )

    if args.json:
        # Convert non-serializable objects in meta if any
        res["meta"] = {k: str(v) if v is not None else None for k, v in res.get("meta", {}).items()}
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(f"Kiểm tra Job #{args.job_id} ({res['num_tracks']} tracks)")
        print_report(res["alerts"], res["merges"], res.get("issue_results"))


def run_webhook_server(args):
    from flask import Flask, request, jsonify

    app = Flask(__name__)
    cfg = load_config(args.config, args.fps)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "healthy", "service": "idcheck-webhook"}), 200

    @app.route("/webhook", methods=["POST"])
    def webhook():
        data = request.get_json(silent=True) or {}
        event = data.get("event", "")
        job_info = data.get("job", {})
        job_id = job_info.get("id")
        stage = job_info.get("stage")

        logger.info("Nhận webhook: event=%s, job_id=%s, stage=%s", event, job_id, stage)

        if event == "ping":
            logger.info("CVAT Ping received! Webhook connection verified.")
            return jsonify({"status": "pong", "message": "IdCheck webhook ready"}), 200

        if not job_id:
            return jsonify({"status": "ignored", "reason": "no_job_id"}), 200

        # Kiểm tra điều kiện: lead chuyển job sang validation
        is_validation = stage == "validation" or (
            event == "update:job" and stage == "validation"
        )

        if not is_validation and not args.force_check:
            return jsonify({
                "status": "ignored",
                "reason": f"stage '{stage}' is not validation",
                "job_id": job_id
            }), 200

        def async_worker(jid):
            try:
                logger.info("Bắt đầu chạy idcheck trên Job #%d...", jid)
                client = get_client(host=args.cvat_host, user=args.cvat_user, password=args.cvat_password)
                res = check_job(
                    client=client,
                    job_id=jid,
                    cfg=cfg,
                    create_issues=args.auto_create_issues,
                    dry_run=args.dry_run,
                    include_merges=args.include_merges,
                    min_score=args.min_score,
                )
                created = sum(1 for r in res.get("issue_results", []) if r.get("status") == "created")
                logger.info("Hoàn thành Job #%d: %d alerts, %d issues đã tạo.", jid, len(res["alerts"]), created)
            except Exception as e:
                logger.exception("Lỗi khi xử lý Job #%d trong webhook: %s", jid, e)

        thread = threading.Thread(target=async_worker, args=(job_id,), daemon=True)
        thread.start()

        return jsonify({"status": "processing", "job_id": job_id, "stage": stage}), 200

    logger.info("Khởi động IdCheck Webhook Server tại http://%s:%d/webhook", args.server_host, args.server_port)
    app.run(host=args.server_host, port=args.server_port)


def cmd_watch(args):
    import time

    cfg = load_config(args.config, args.fps)
    client = get_client(host=args.host, user=args.user, password=args.password)

    logger.info("Bắt đầu giám sát CVAT (chu kỳ quét: %ds)... Nhấn Ctrl+C để dừng.", args.interval)
    checked_states = {}

    while True:
        try:
            jobs = client.jobs.list()
            for job in jobs:
                stage = getattr(job, "stage", None)
                if stage == "validation":
                    jid = job.id
                    updated = str(getattr(job, "updated_date", ""))
                    if checked_states.get(jid) != updated:
                        logger.info("Phát hiện Job #%d ở stage 'validation'. Bắt đầu kiểm tra...", jid)
                        res = check_job(
                            client=client,
                            job_id=jid,
                            cfg=cfg,
                            create_issues=not args.dry_run,
                            dry_run=args.dry_run,
                            include_merges=args.include_merges,
                            min_score=args.min_score,
                        )
                        created = sum(1 for r in res.get("issue_results", []) if r.get("status") == "created")
                        skipped = sum(1 for r in res.get("issue_results", []) if r.get("status") == "skipped")
                        logger.info(
                            "Job #%d: %d cảnh báo | Đã tạo %d issues, bỏ qua %d issues.",
                            jid,
                            len(res["alerts"]),
                            created,
                            skipped,
                        )
                        checked_states[jid] = updated
                else:
                    if job.id in checked_states:
                        del checked_states[job.id]
        except Exception as e:
            logger.error("Lỗi trong chu kỳ quét: %s", e)

        if args.once:
            break
        time.sleep(args.interval)


def cmd_calibrate(args):
    from tools.tune.calibrate_project import calibrate_project

    client = get_client(host=args.host, user=args.user, password=args.password)
    calibrate_project(
        client=client,
        project_id=args.project,
        job_ids=args.jobs,
        fps=args.fps or 5.0,
        base_config_path=args.base_config,
        out_config_path=args.out,
    )


def cmd_studio(args):
    import studio
    from studio.server import Handler, ThreadingHTTPServer
    import webbrowser
    import threading

    url = f"http://{args.host}:{args.port}"
    logger.info("Khởi chạy idcheck QA Studio tại %s", url)
    print("\n" + "=" * 60)
    print(" idcheck QA Studio (Web UI + idcheck core rules)")
    print(f" URL: {url}")
    print(" Dừng server bằng tổ hợp phím Ctrl+C")
    print("=" * 60 + "\n", flush=True)

    if not args.no_browser and args.host in ("127.0.0.1", "localhost"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Đã dừng idcheck QA Studio.")
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass


def build_parser():
    parser = argparse.ArgumentParser(description="IdCheck - Công cụ kiểm tra chất lượng nhãn video tracking cho CVAT")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: check-xml
    p_xml = subparsers.add_parser("check-xml", help="Kiểm tra file CVAT for video 1.1 XML xuất từ CVAT")
    p_xml.add_argument("xml_file", help="Đường dẫn file .xml")
    p_xml.add_argument("-c", "--config", default="config_base.yaml", help="Đường dẫn file cấu hình YAML")
    p_xml.add_argument("--min-score", type=float, default=0.5, help="Ngưỡng score tối thiểu cho merge suggestion")
    p_xml.add_argument("--no-interpolate", action="store_true", help="Không nội suy tuyến tính giữa các keyframe")
    p_xml.add_argument("--json", action="store_true", help="Xuất kết quả định dạng JSON")
    p_xml.set_defaults(func=cmd_check_xml)

    # Subcommand: check-job
    p_job = subparsers.add_parser("check-job", help="Kiểm tra trực tiếp một Job trên CVAT thông qua API")
    p_job.add_argument("job_id", type=int, help="ID của Job trên CVAT")
    p_job.add_argument("-c", "--config", default="config_base.yaml", help="Đường dẫn file cấu hình YAML")
    p_job.add_argument("--host", default=None, help="Địa chỉ CVAT host (mặc định lấy từ CVAT_HOST)")
    p_job.add_argument("--user", default=None, help="Tài khoản CVAT (mặc định lấy từ CVAT_USER)")
    p_job.add_argument("--password", default=None, help="Mật khẩu CVAT (mặc định lấy từ CVAT_PASS)")
    p_job.add_argument("--create-issues", action="store_true", help="Tự động tạo Issue trên CVAT tại các frame phát hiện lỗi")
    p_job.add_argument("--dry-run", action="store_true", help="Mô phỏng quá trình tạo Issue (không ghi lên server)")
    p_job.add_argument("--include-merges", action=argparse.BooleanOptionalAction, default=True,
                   help="Tạo cả Issue cho các gợi ý nối track (mặc định bật, tắt bằng --no-include-merges)")
    p_job.add_argument("--min-score", type=float, default=0.5, help="Ngưỡng score tối thiểu cho merge suggestion")
    p_job.add_argument("--json", action="store_true", help="Xuất kết quả định dạng JSON")
    p_job.set_defaults(func=cmd_check_job)

    # Subcommand: watch (polling tự động)
    p_watch = subparsers.add_parser("watch", help="Tự động theo dõi các Job trên CVAT và kiểm tra khi chuyển sang Validation")
    p_watch.add_argument("-c", "--config", default="config_base.yaml", help="Đường dẫn cấu hình YAML")
    p_watch.add_argument("--host", default=None, help="Địa chỉ CVAT host")
    p_watch.add_argument("--user", default=None, help="Tài khoản CVAT")
    p_watch.add_argument("--password", default=None, help="Mật khẩu CVAT")
    p_watch.add_argument("--interval", type=int, default=10, help="Thời gian lặp quét (giây, mặc định 10s)")
    p_watch.add_argument("--dry-run", action="store_true", help="Mô phỏng, không ghi issue lên CVAT")
    p_watch.add_argument("--include-merges", action=argparse.BooleanOptionalAction, default=True,
                     help="Tạo cả Issue cho các gợi ý nối track (mặc định bật, tắt bằng --no-include-merges)")
    p_watch.add_argument("--min-score", type=float, default=0.5, help="Ngưỡng score tối thiểu cho merge suggestion")
    p_watch.add_argument("--once", action="store_true", help="Chỉ quét 1 lần rồi thoát")
    p_watch.set_defaults(func=cmd_watch)

    # Subcommand: server (webhook)
    p_srv = subparsers.add_parser("server", help="Khởi chạy Webhook Server lắng nghe sự kiện từ CVAT")
    p_srv.add_argument("--server-host", default="0.0.0.0", help="Host lắng nghe (mặc định 0.0.0.0)")
    p_srv.add_argument("-p", "--server-port", type=int, default=5000, help="Cổng lắng nghe (mặc định 5000)")
    p_srv.add_argument("-c", "--config", default="config_base.yaml", help="Đường dẫn cấu hình YAML")
    p_srv.add_argument("--cvat-host", default=None, help="Địa chỉ CVAT host")
    p_srv.add_argument("--cvat-user", default=None, help="Tài khoản CVAT")
    p_srv.add_argument("--cvat-password", default=None, help="Mật khẩu CVAT")
    p_srv.add_argument("--auto-create-issues", action="store_true", default=True, help="Tự động tạo issues khi job sang validation")
    p_srv.add_argument("--dry-run", action="store_true", help="Chế độ mô phỏng, không ghi issue lên CVAT")
    p_srv.add_argument("--include-merges", action=argparse.BooleanOptionalAction, default=True,
                   help="Tạo cả Issue cho các gợi ý nối track (mặc định bật, tắt bằng --no-include-merges)")
    p_srv.add_argument("--min-score", type=float, default=0.5, help="Ngưỡng score tối thiểu cho merge suggestion")
    p_srv.add_argument("--force-check", action="store_true", help="Chạy kiểm tra trên mọi sự kiện không phân biệt stage")
    p_srv.set_defaults(func=run_webhook_server)

    # Subcommand: calibrate (Auto-tuning từ jobs acceptance)
    p_cal = subparsers.add_parser("calibrate", help="Tự động hiệu chỉnh ngưỡng từ các Job Ground Truth trên CVAT")
    p_cal.add_argument("--project", type=int, required=True, help="Project ID trên CVAT")
    p_cal.add_argument("--jobs", type=int, nargs="+", default=None, help="Danh sách Job ID chỉ định (mặc định lấy các job stage=acceptance)")
    p_cal.add_argument("-c", "--base-config", default="config_base.yaml", help="File cấu hình nền")
    p_cal.add_argument("-o", "--out", default=None, help="File cấu hình đầu ra xuất bản")
    p_cal.add_argument("--host", default=None, help="Địa chỉ CVAT host")
    p_cal.add_argument("--user", default=None, help="Tài khoản CVAT")
    p_cal.add_argument("--password", default=None, help="Mật khẩu CVAT")
    p_cal.add_argument("--fps", type=float, default=5.0, help="FPS của video (mặc định 5.0 cho BDD100K)")
    # Subcommand: studio (Web QA Studio)
    p_studio = subparsers.add_parser("studio", aliases=["web"], help="Khởi chạy idcheck QA Studio (Giao diện Web)")
    p_studio.add_argument("--host", default="127.0.0.1", help="Host lắng nghe (mặc định 127.0.0.1, dùng 0.0.0.0 để mở ra mạng ngoài)")
    p_studio.add_argument("-p", "--port", type=int, default=8765, help="Cổng chạy server (mặc định 8765)")
    p_studio.add_argument("--no-browser", action="store_true", help="Không tự động mở trình duyệt")
    p_studio.set_defaults(func=cmd_studio)

    for sp in (p_xml, p_job, p_watch, p_srv):
        sp.add_argument("--fps", type=float, default=None,
                        help="fps của nhãn (ví dụ 5 cho BDD100K MOT, 25 cho MOT20)")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
