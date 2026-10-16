# IDCHECK - Bộ Công Cụ Tự Động Kiểm Tra & Đảm Bảo Chất Lượng Gán Nhãn MOT Trên CVAT

`idcheck` là hệ thống kiểm tra chất lượng tự động (Quality Assurance) cho dữ liệu gán nhãn tracking đa đối tượng (Multi-Object Tracking - MOT). Công cụ kết nối trực tiếp với **CVAT qua API (cvat-sdk)** hoặc làm việc ngoại tuyến thông qua file **CVAT XML for video 1.1**.

---

## 1. Tính Năng & Các Loại Lỗi Phát Hiện

Hệ thống dựa trên quy tắc hình học và động học (Rule-based) được tối ưu hóa đặc biệt cho video camera hành trình (dashcam - BDD100K) và video tĩnh/người đi bộ (MOT17/MOT20):

| Loại Cảnh Báo | Mô Tả | Cơ Chế Xử Lý Thông Minh |
|---|---|---|
| **GAP** | Bỏ sót nhãn trong một số frame giữa chừng của track. | Bỏ qua nếu đối tượng bị che khuất (`occluded=True`) hoặc đã ra ngoài viền (`outside=True`). |
| **JUMP** | Bounding box nhảy đột ngột (tốc độ vượt ngưỡng cho phép). | Tự động **bù trừ chuyển động camera** (`compensate_camera`) khi xe rẽ/quay và nhận diện trôi đều góc mép (`is_smooth_drift`) để không báo nhầm khi xe lạ trượt vào khung hình. |
| **SCALE** | Kích thước box thay đổi đột ngột giữa 2 frame liên tiếp. | Bỏ qua nếu có cờ `occluded=True` (tránh bắt nhầm khi đối tượng bị cột/xe khác che khuất một phần). |
| **SWITCH** (Swap) | Hai track đi ngang qua/gần nhau và bị hoán đổi ID cho nhau. | Kiểm tra khoảng cách tâm, tỷ lệ cắt chéo và vận tốc di chuyển tương đối. |
| **DUPLICATE** | Hai track khác nhau nhưng gán đè lên cùng 1 đối tượng thực tế. | Tính toán IoU cao liên tục trong nhiều frame liên tiếp. |
| **MERGE SUGGESTION** | Gợi ý nối 2 track bị gãy (fragmented tracks) thành 1 track thống nhất. | Dự đoán vận tốc tuyến tính, phạt điểm mép biên (`border_penalty`), yêu cầu số frame tối thiểu (`min_merge_track_len`) để tránh nhầm lẫn giữa người vào/người ra. |

---

## 2. Cài Đặt Môi Trường

### 2.1. Yêu Cầu Hệ Thống
- Python 3.10+ (khuyến nghị Python 3.12+).
- CVAT Server 2.74+ và tài khoản có quyền truy cập các Task/Job cần kiểm tra.

### 2.2. Cài Đặt Thư Viện
```bash
pip install -r requirements.txt
```
*(Các thư viện chính bao gồm: `cvat-sdk`, `pyyaml`, `scipy`, `numpy`, `flask`, `pytest`)*

### 2.3. Cấu Hình Tài Khoản CVAT
Tạo file `.env` tại thư mục gốc của project (hoặc thiết lập biến môi trường):
```env
CVAT_HOST=https://cvat.trantuli.id.vn
CVAT_USER=BOT
CVAT_PASS=your_password_here
```

---

## 3. Cấu Trúc File Cấu Hình (YAML Config)

Tất cả các ngưỡng thời gian trong hệ thống được chuẩn hóa theo **đơn vị giây (seconds)**, cho phép tự động thích ứng với bất kỳ tốc độ khung hình (FPS) nào thông qua bộ chuyển đổi `resolve_cfg(cfg, fps)`:

- [config_base.yaml](file:///home/tran-tuan-kinh/idcheck/config_base.yaml): File cấu hình gốc chuẩn hóa theo giây (mặc định dùng 30 FPS).
- [config_project_3.yaml](file:///home/tran-tuan-kinh/idcheck/config_project_3.yaml): File cấu hình đã hiệu chỉnh và tối ưu hóa riêng cho **Project 3 (BDD100K MOT - 5 FPS)** với các điều kiện dashcam ban ngày.
- [config_mot17.yaml](file:///home/tran-tuan-kinh/idcheck/config_mot17.yaml): File cấu hình cho tập benchmark MOT17.

### Các tham số quan trọng:
```yaml
default:
  min_missing_gap_s: 0.45    # Thiếu >= 0.45s mới tính là GAP
  max_missing_gap_s: 0.5     # Thiếu > 0.5s coi như mất track (chuyển sang xét MERGE)
  max_frame_step_s: 0.1      # Khoảng thời gian tối đa giữa 2 frame liên tiếp để xét JUMP
  max_speed: 15.0            # Vận tốc tối đa (số lần kích thước box / giây)
  max_scale: 2.2             # Tỷ lệ biến thiên diện tích tối đa giữa 2 frame
  skip_occluded_gap: true    # Bỏ qua gap nếu box trước/sau bị che khuất
  skip_occluded_scale: true  # Bỏ qua cảnh báo scale nếu box bị che khuất
  compensate_camera: true    # Bù chuyển động nền của camera xe chạy
  max_merge_gap_s: 0.8       # Thời gian ngắt quãng tối đa để gợi ý ghép nối track
  min_merge_track_len: 4     # Độ dài track tối thiểu mới cho phép gợi ý nối
  border_margin: 30          # Khoảng cách px tính từ 4 mép khung hình
  border_penalty: 3.0        # Điểm phạt khi đối tượng biến mất và xuất hiện cùng mép
```

---

## 4. Hướng Dẫn Sử Dụng Chi Tiết (CLI)

Điểm vào chính của công cụ là [cli.py](file:///home/tran-tuan-kinh/idcheck/cli.py).

### 4.1. Kiểm Tra Trực Tiếp Một Job Trên CVAT (`check-job`)

Kiểm tra trực tiếp một Job thông qua CVAT REST API:

```bash
# 1. Chỉ kiểm tra và in báo cáo ra màn hình (không thay đổi gì trên CVAT)
python cli.py check-job <JOB_ID> -c config_project_3.yaml --fps 5.0

# Ví dụ kiểm tra Job 23:
python cli.py check-job 23 -c config_project_3.yaml --fps 5.0

# 2. Chế độ DRY-RUN: Giả lập việc tạo Issue lên CVAT để xem trước các vị trí lỗi
python cli.py check-job 23 -c config_project_3.yaml --fps 5.0 --create-issues --dry-run

# 3. TỰ ĐỘNG TẠO ISSUE: Tạo comment và issue trực tiếp tại các frame lỗi trên CVAT
python cli.py check-job 23 -c config_project_3.yaml --fps 5.0 --create-issues
```

*Tùy chọn bổ sung:*
- `--no-include-merges`: Chỉ tạo Issue cho các lỗi vi phạm (`JUMP`, `GAP`, `SCALE`, `SWITCH`), không tạo Issue cho các gợi ý nối track (`MERGE SUGGESTION`).

---

### 4.2. Giám Sát Tự Động Định Kỳ Trên CVAT (`watch`)

Chế độ Polling Daemon giúp giám sát liên tục các Job trong Project. Khi Reviewer hoặc Annotator chuyển Job sang stage `validation` (hoặc `acceptance`), tool sẽ tự động quét, kiểm tra chất lượng và ghim Issue báo lỗi để Reviewer xử lý ngay:

```bash
# Giám sát Project 3, quét mỗi 30 giây một lần, tự động tạo Issue khi phát hiện lỗi
python cli.py watch -c config_project_3.yaml --fps 5.0 --project-id 3 --interval 30 --create-issues

# Chạy thử 1 lần duy nhất (One-shot) với chế độ Dry-run
python cli.py watch -c config_project_3.yaml --fps 5.0 --project-id 3 --once --dry-run
```

#### Chạy Ngầm Trong Background (Production / Server):
```bash
nohup python cli.py watch -c config_project_3.yaml --fps 5.0 --project-id 3 --interval 30 --create-issues > watch.log 2>&1 &
```
*Để kiểm tra log:* `tail -f watch.log`  
*Để dừng tiến trình:* `pkill -f "cli.py watch"`

---

### 4.3. Kiểm Tra File XML Ngoại Tuyến (`check-xml`)

Sử dụng khi xuất file dữ liệu từ CVAT theo định dạng `CVAT for video 1.1 XML`:

```bash
# In báo cáo tóm tắt trên terminal
python cli.py check-xml path/to/annotations.xml -c config_project_3.yaml --fps 5.0

# Xuất kết quả dưới định dạng JSON (dùng cho tích hợp CI/CD pipeline)
python cli.py check-xml path/to/annotations.xml -c config_project_3.yaml --fps 5.0 --json > result.json
```

---

### 4.4. Tự Động Tinh Chỉnh Ngưỡng Theo Dữ Liệu Thực Tế (`calibrate`)

Lấy các Job đã được nghiệm thu (Ground Truth có stage `acceptance` hoặc `completed`) trong Project để tự động tính toán phân phối thống kê (vận tốc di chuyển, tỷ lệ scale, khoảng cách dừng...) và sinh file cấu hình tối ưu:

```bash
# Tự động học ngưỡng từ các Job sạch của Project 3 và xuất ra file cấu hình mới
python cli.py calibrate 3 -o config_project_3.yaml --fps 5.0
```

---

### 4.5. Khởi Chạy Webhook Server (`server`)

Nhận sự kiện trực tiếp từ CVAT Webhook (`job:updated`) theo thời gian thực thay vì chạy vòng lặp polling:

```bash
python cli.py server -p 5000 -c config_project_3.yaml --fps 5.0 --create-issues
```

---

## 5. Công Cụ Hỗ Trợ Chẩn Đoán & Debug

Khi cần phân tích sâu tại sao một cặp Track $A \rightarrow B$ lại được hoặc không được gợi ý ghép nối:

```bash
python -m tools.diagnose.explain_pair cvat <JOB_ID> 5.0 config_project_3.yaml --a <TRACK_A_ID> --b <TRACK_B_ID>
```
Lệnh sẽ in ra bảng phân tích chi tiết từng tiêu chí:
- Khoảng cách frame giữa 2 track ($gap$).
- Tỷ lệ kích thước bounding box ($scale\_ratio$).
- Chi phí dự đoán vị trí ($cost$) so với ngưỡng $merge\_max\_cost$.
- Phạt mép biên ($border\_penalty$) nếu cả hai cùng ở sát viền màn hình.

---

## 6. Kiểm Thử Tự Động (Unit Tests)

Bộ kiểm thử bao gồm 47 test case bảo vệ toàn diện các chức năng core, rule, adapter và CLI:

```bash
python -m pytest
```
