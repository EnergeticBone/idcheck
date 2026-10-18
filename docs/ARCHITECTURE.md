# KIẾN TRÚC HỆ THỐNG IDCHECK (SYSTEM ARCHITECTURE)

> **Dự án**: `idcheck` — Automated Quality Assurance & Verification System for Multi-Object Tracking (MOT) on CVAT  
> **Phiên bản**: 2.0 (Hỗ trợ Dashcam BDD100K, Re-ID Deep Feature, Role-Based Access Control)  
> **Tác giả**: Tran Tuan Kinh  
> **Ngày cập nhật**: 18/10/2026

---

## 1. Tổng Quan Kiến Trúc (System Overview)

Hệ thống **`idcheck`** là nền tảng kiểm thử và đảm bảo chất lượng tự động (QA/QC) chuyên dụng cho dữ liệu gán nhãn theo dõi đa đối tượng (Multi-Object Tracking - MOT). Hệ thống được thiết kế theo mô hình kiến trúc nhiều tầng (Layered Architecture) với độ ghép nối lỏng (Loose Coupling), phục vụ hai phương thức vận hành chính:

1. **Vận hành Ngoại tuyến & Tự động (Offline & Automated Daemons)**: Quét file XML, chạy daemon giám sát liên tục (`watch`) qua CVAT REST API, tự động tinh chỉnh ngưỡng (`calibrate`), chẩn đoán chuyên sâu (`diagnose`).
2. **Nền tảng Tương tác Trực tuyến (Interactive Web Studio)**: Web Studio tương tác thời gian thực, hỗ trợ phân quyền vai trò (Annotator, Reviewer, Lead/Admin), trực quan hóa bounding box, phát hiện lỗi tự động và đồng bộ hai chiều với CVAT server.

---

## 2. Sơ Đồ Kiến Trúc Tổng Thể (System Architecture Diagram)

Dưới đây là sơ đồ kiến trúc thể hiện toàn bộ các tầng logic, thành phần module và tương tác dữ liệu trong hệ thống `idcheck`:

```mermaid
graph TB
    subgraph Client_Layer ["1. TẦNG TRÌNH DIỄN & GIAO DIỆN (CLIENT & PRESENTATION LAYER)"]
        UI_Web["idcheck Studio (SPA Web GUI)<br/>• Canvas 2D Video Player<br/>• Interactive Zoom & Bounding Box<br/>• Issue Audit Drawer<br/>• Organization & Role Switcher"]
        CLI_App["CLI Application (cli.py)<br/>• check-job / check-xml<br/>• watch daemon<br/>• calibrate / benchmark<br/>• server (webhook)"]
    end

    subgraph Service_Layer ["2. TẦNG ĐIỀU PHỐI & NGHIỆP VỤ (STUDIO & ORCHESTRATION LAYER)"]
        Server["Studio HTTP Server (server.py)<br/>WSGI / RESTful API Engine"]
        Auth_Mod["Module Phân Quyền (auth.py)<br/>• Session & Token Management<br/>• Role-Based Access Control (RBAC)"]
        Workflow_Mod["CVAT Workflow Engine (cvat_workflow.py)<br/>• Job Stage Transition (annotation ⇄ validation)<br/>• Role Isolation (Annotator vs Reviewer)<br/>• Reject & Raise Issues to CVAT<br/>• Job-level Metadata Persistence"]
        Orchestrator["Core Orchestrator (orchestrator.py)<br/>• Box Normalization & Gating<br/>• Rule Execution Pipeline<br/>• Re-ID Integration<br/>• Issue Mapping & Suggestions"]
        Benchmark_Mod["Benchmark Engine (benchmark.py)<br/>• GT Comparison<br/>• Precision / Recall / F1 Analytics"]
    end

    subgraph Core_Engine ["3. TẦNG LÕI THUẬT TOÁN (CORE ALGORITHMS & RULES LAYER)"]
        Rules_Engine["Rule-based Engine (core/rules.py)<br/>• check_gaps (Keyframe-aware)<br/>• check_jumps (Spike & Drift)<br/>• check_duplicates (IoU Overlap)<br/>• check_switches (Crossing Tracks)<br/>• check_class_flips<br/>• check_invalid_boxes<br/>• Horizon-Aware Filter"]
        Cam_Comp["Bù Trừ Chuyển Động Camera<br/>(Global Motion Compensation)"]
        Merge_Engine["Track Reconnection (core/merge.py)<br/>• Linear Sum Assignment (Hungarian)<br/>• Velocity Projection & Border Penalty"]
        ReID_Engine["Visual Re-ID Engine (core/reid.py)<br/>• Deep Feature Extraction (OSNet / ResNet)<br/>• Visual Cosine Similarity<br/>• False Switch Pruner & Long-gap Merge"]
        Config_Engine["Config Engine (core/config.py)<br/>• Dynamic Second-to-Frame Scaling<br/>• Multi-class & Horizon Overrides"]
        Inject_Engine["Fault Injection Engine (core/inject.py)<br/>• Synthetic Gap, Jump, Switch, Merge Injector"]
    end

    subgraph Integration_Layer ["4. TẦNG KẾT NỐI & DỮ LIỆU NGOÀI (ADAPTERS LAYER)"]
        CVAT_API["CVAT API Adapter (adapters/cvat_api.py)<br/>• cvat-sdk REST Client<br/>• Track Loader (Linear Interpolation Handling)<br/>• Issue & Comment Sync Engine"]
        CVAT_XML["CVAT XML Adapter (adapters/cvat_xml.py)<br/>• CVAT for video 1.1 Parser & Serializer"]
        MOT_Adapter["MOT Adapter (adapters/mot.py)<br/>• MOT17 / MOT20 Challenge Format"]
    end

    subgraph Storage_External ["5. HẠ TẦNG LƯU TRỮ & HỆ THỐNG NGOẠI VI"]
        CVAT_Server[("CVAT Server (v2.74+)<br/>cvat.trantuli.id.vn<br/>Projects / Tasks / Jobs / Issues")]
        YAML_Configs[("YAML Configurations<br/>• config_base.yaml<br/>• config_project_3.yaml<br/>• config_mot17.yaml")]
        Frame_Cache[("Local Frame & Meta Storage<br/>• storage/cvat_frames/<br/>• storage/cvat_job_meta.json")]
    end

    %% Client to Service
    UI_Web -->|HTTP / JSON API| Server
    CLI_App -->|Direct Python Call| Workflow_Mod
    CLI_App -->|Direct Python Call| Orchestrator

    %% Service internal
    Server --> Auth_Mod
    Server --> Workflow_Mod
    Server --> Orchestrator
    Server --> Benchmark_Mod

    %% Service to Core
    Orchestrator --> Rules_Engine
    Orchestrator --> Merge_Engine
    Orchestrator --> ReID_Engine
    Orchestrator --> Config_Engine
    Workflow_Mod --> Orchestrator
    Benchmark_Mod --> Inject_Engine

    %% Core relationships
    Rules_Engine --> Cam_Comp
    Rules_Engine --> Config_Engine
    Merge_Engine --> Config_Engine

    %% Service/Core to Adapters
    Workflow_Mod --> CVAT_API
    Orchestrator --> CVAT_API
    CLI_App --> CVAT_XML
    CLI_App --> MOT_Adapter

    %% Adapters to External
    CVAT_API <-->|REST API / HTTPS| CVAT_Server
    Config_Engine <-->|Read YAML| YAML_Configs
    Workflow_Mod <-->|Cache & Read/Write| Frame_Cache
    ReID_Engine <-->|Fetch Frame Crops| Frame_Cache
```

---

## 3. Kiến Trúc Luồng Dữ Liệu & Nghiệp Vụ (Data Flow Architecture)

### 3.1. Quy Trình Phân Vai & Kiểm Thẩm Định Chất Lượng (QC Workflow)

Hệ thống phân tách nghiêm ngặt không gian làm việc giữa **Annotator** (người gán nhãn) và **Reviewer** (người duyệt/kiểm soát chất lượng):

```mermaid
sequenceDiagram
    autonumber
    actor Annotator as Người Gán Nhãn (Annotator)
    actor Reviewer as Kiểm Định Viên (Reviewer / Lead)
    participant Studio as idcheck Studio Server
    participant Core as Core Rule & Re-ID Engine
    participant CVAT as CVAT Server API

    Note over Annotator, Reviewer: 1. GIAI ĐOẠN GÁN NHÃN (Stage: annotation)
    Annotator->>Studio: Mở Job được giao (stage = annotation)
    Studio->>CVAT: Truy vấn danh sách Issue hiện có trên Job
    CVAT-->>Studio: Danh sách Issue đã tạo từ trước
    Studio-->>Annotator: Chỉ hiển thị các Issue chính thức do Reviewer tạo (KHÔNG chạy tool quét thô)
    Annotator->>CVAT: Sửa bounding box trên CVAT giao diện chuẩn
    Annotator->>Studio: Bấm "Nộp thẩm định" (Submit Validation)
    Studio->>CVAT: Cập nhật Job stage = 'validation', state = 'completed'

    Note over Annotator, Reviewer: 2. GIAI ĐOẠN KIỂM SOÁT CHẤT LƯỢNG (Stage: validation)
    Reviewer->>Studio: Mở Job cần thẩm định (stage = validation)
    Studio->>CVAT: Tải tracks (keyframe & interpolated boxes) + metadata
    CVAT-->>Studio: Dữ liệu track và kích thước frame
    Studio->>Core: Kích hoạt quét tự động (Rules + Hungarian Merge + Re-ID Filter)
    Core-->>Studio: Danh sách lỗi: Gap, Jump, Duplicate, Swap, Switch, Merge Suggestions
    Studio-->>Reviewer: Hiển thị bảng Audit Issue + Preview trực quan trên video

    alt Reviewer phát hiện lỗi không đạt chất lượng (Từ chối)
        Reviewer->>Studio: Chọn các Issue cần bắt buộc sửa -> Bấm "Từ chối & Tạo Issues"
        Studio->>CVAT: Tạo Issues & Comments chi tiết tại đúng frame và tọa độ box
        Studio->>CVAT: Cập nhật Job stage = 'annotation', state = 'rejected'
        CVAT-->>Annotator: Thông báo Job bị trả về kèm danh sách Issue cần khắc phục
    else Dữ liệu đạt tiêu chuẩn chất lượng (Phê duyệt)
        Reviewer->>Studio: Bấm "Phê duyệt Job" (Approve)
        Studio->>CVAT: Cập nhật Job stage = 'acceptance', state = 'completed'
        CVAT-->>Reviewer: Job hoàn tất nghiệm thu và khóa nhãn
    end
```

---

### 3.2. Đường Ống Xử Lý Lỗi (Core Detection Pipeline)

Đường ống xử lý dữ liệu từ CVAT đến khi sinh ra danh sách lỗi hoàn chỉnh:

```mermaid
flowchart TD
    RawData["Dữ liệu thô từ CVAT API / XML"] --> LoadTracks["Nạp Track (load_job_tracks)<br/>Tách bạch Keyframe thực và Box nội suy"]
    LoadTracks --> Normalizer["Chuẩn hóa Bounding Box (models.normalize_boxes)"]
    Normalizer --> ConfigResolver["Chuyển đổi Ngưỡng Thời gian (core/config.resolve_cfg)<br/>Quy đổi giây (s) sang frame dựa theo FPS video"]
    
    ConfigResolver --> RuleDispatch{"Phân bổ kiểm tra Quy tắc"}
    
    RuleDispatch --> R_Invalid["Kiểm tra tính toàn vẹn hình học<br/>(check_invalid_boxes)"]
    RuleDispatch --> R_Gap["Kiểm tra thiếu nhãn<br/>(check_gaps trên Keyframe thực)"]
    RuleDispatch --> R_Jump["Kiểm tra văng/nhảy box<br/>(check_jumps + bù chuyển động camera)"]
    RuleDispatch --> R_Flip["Kiểm tra đổi class<br/>(check_class_flips)"]
    RuleDispatch --> R_Dup["Kiểm tra trùng lặp box<br/>(check_duplicates IoU)"]
    RuleDispatch --> R_Swap["Kiểm tra tráo đổi ID<br/>(check_switches)"]
    RuleDispatch --> R_Merge["Gợi ý ghép nối Track gãy<br/>(core/merge.suggest_merges)"]

    R_Swap --> ReID_Gate{"Re-ID Size Gating & Filter"}
    R_Merge --> ReID_Gate
    
    ReID_Gate -->|Đạt ngưỡng diện tích| ReID_Extract["Trích xuất Vector Đặc trưng Thị giác (ResNet/OSNet)<br/>Tính Cosine Similarity giữa các Track"]
    ReID_Gate -->|Dưới ngưỡng diện tích| RawCombine["Tổng hợp Issue thô"]
    
    ReID_Extract --> ReID_Prune["Lọc bỏ False Positive Switch<br/>(Loại trừ báo nhầm xe giống nhau)"]
    ReID_Extract --> ReID_Merge["Bổ sung Gợi ý Nối Track qua Gap dài (Visual Merge)"]
    
    ReID_Prune --> FinalIssues["Danh sách Issue Hoàn Chỉnh (Format Chuẩn Studio & CVAT)"]
    ReID_Merge --> FinalIssues
    R_Invalid --> FinalIssues
    R_Gap --> FinalIssues
    R_Jump --> FinalIssues
    R_Flip --> FinalIssues
    R_Dup --> FinalIssues
```

---

## 4. Đặc Tả Từng Thành Phần Hệ Thống (Component Specifications)

| Tầng (Layer) | Module / Tệp Nguồn | Vai Trò & Nhiệm Vụ Kỹ Thuật | Phụ Thuộc (Dependencies) |
|---|---|---|---|
| **Presentation** | [`studio/static/index.html`](file:///home/tran-tuan-kinh/idcheck/studio/static/index.html)<br/>[`studio/static/app.js`](file:///home/tran-tuan-kinh/idcheck/studio/static/app.js)<br/>[`studio/static/style.css`](file:///home/tran-tuan-kinh/idcheck/studio/static/style.css) | Giao diện SPA hiện đại, Dark-mode glassmorphic, tương tác canvas vẽ bounding box, scrubber timeline video, bảng phân tích issue, điều khiển zoom-to-box mượt mà. | HTML5 Canvas, Vanilla JS/CSS |
| **Presentation** | [`cli.py`](file:///home/tran-tuan-kinh/idcheck/cli.py) | CLI entrypoint cung cấp các subcommand: `check-job`, `check-xml`, `watch`, `calibrate`, `server`, `studio`, `benchmark`. | `argparse`, `core.*`, `adapters.*` |
| **Orchestration** | [`studio/server.py`](file:///home/tran-tuan-kinh/idcheck/studio/server.py) | Máy chủ HTTP REST API, định tuyến các endpoint xác thực, danh sách job, nạp frame media, quét lỗi và nhận lệnh kiểm duyệt. | `http.server`, `urllib` |
| **Orchestration** | [`studio/backend/auth.py`](file:///home/tran-tuan-kinh/idcheck/studio/backend/auth.py) | Quản lý phiên đăng nhập (Session Token), xác thực với CVAT API, RBAC (Admin, Annotator, Reviewer). | `hashlib`, `secrets`, `time` |
| **Orchestration** | [`studio/backend/cvat_workflow.py`](file:///home/tran-tuan-kinh/idcheck/studio/backend/cvat_workflow.py) | Cầu nối nghiệp vụ với CVAT: Tải job, nạp frame cache, phân định quyền truy cập ở mức Job, thực thi tạo Issue/Comment trực tiếp trên CVAT khi từ chối, chuyển đổi stage. | `adapters.cvat_api`, `core.config` |
| **Orchestration** | [`studio/backend/orchestrator.py`](file:///home/tran-tuan-kinh/idcheck/studio/backend/orchestrator.py) | Điều phối chu trình quét: Chuẩn hóa dữ liệu box, nạp cấu hình YAML phù hợp FPS, gọi thuật toán lõi, tích hợp trích xuất Re-ID và ánh xạ sang định dạng Issue của Studio. | `core.rules`, `core.merge`, `core.reid` |
| **Core Algorithm** | [`core/rules.py`](file:///home/tran-tuan-kinh/idcheck/core/rules.py) | Thực thi 6 quy tắc hình học - động học cơ bản: Keyframe-aware gap detection, camera motion compensation, jump spike/drift detection, class flips, duplicates, horizon-aware small box filter. | `math`, `statistics`, `collections` |
| **Core Algorithm** | [`core/merge.py`](file:///home/tran-tuan-kinh/idcheck/core/merge.py) | Thuật toán ghép nối track (Track Reconnection): Dự đoán quỹ đạo tuyến tính, phạt đối tượng ra/vào mép màn hình (`border_penalty`), giải bài toán gán cặp tối ưu bằng thuật toán Hungarian. | `scipy.optimize.linear_sum_assignment`, `numpy` |
| **Core Algorithm** | [`core/reid.py`](file:///home/tran-tuan-kinh/idcheck/core/reid.py) | Trích xuất vector đặc trưng ngoại hình sâu (Deep Visual Embeddings), tính độ tương đồng Cosine, lọc bỏ false positive switch và tìm kiếm mối nối track qua khoảng ngắt quãng dài. | `cv2`, `numpy`, `torch` / TorchVision |
| **Core Algorithm** | [`core/config.py`](file:///home/tran-tuan-kinh/idcheck/core/config.py) | Bộ chuẩn hóa và chuyển đổi cấu hình theo giây (`_s`) sang số frame tương ứng theo FPS thực tế của từng video chuỗi. | `copy` |
| **Core Algorithm** | [`core/inject.py`](file:///home/tran-tuan-kinh/idcheck/core/inject.py) | Engine tiêm lỗi giả lập (Synthetic Fault Injection) phục vụ tạo bộ dữ liệu Ground Truth kiểm thử và benchmark. | `random`, `copy` |
| **Adapters** | [`adapters/cvat_api.py`](file:///home/tran-tuan-kinh/idcheck/adapters/cvat_api.py) | Giao tiếp với CVAT REST API thông qua `cvat-sdk`: Tải track có xử lý cờ nội suy, tải ảnh frame, tạo issue/comment, cập nhật metadata job. | `cvat-sdk`, `requests` |
| **Adapters** | [`adapters/cvat_xml.py`](file:///home/tran-tuan-kinh/idcheck/adapters/cvat_xml.py) | Đọc và ghi file định dạng `CVAT for video 1.1 XML` cho chế độ làm việc offline không cần mạng. | `xml.etree.ElementTree` |

---

## 5. Kiến Trúc Triển Khai & Vận Hành Hạ Tầng (Deployment Architecture)

Hệ thống được cấu hình triển khai trên nền tảng Linux Server:

```mermaid
graph LR
    subgraph Internet_Users ["Người Dùng Internet"]
        Annotator_Client["Annotator Browser"]
        Reviewer_Client["Reviewer / Lead Browser"]
    end

    subgraph Reverse_Proxy ["Nginx Reverse Proxy & SSL Gateway"]
        Nginx["Nginx (Port 80 / 443)<br/>SSL Certificates (Let's Encrypt)<br/>• studio.trantuli.id.vn ➜ 127.0.0.1:8766<br/>• cvat.trantuli.id.vn ➜ CVAT Docker Core"]
    end

    subgraph Internal_Host ["Máy Chủ Dịch Vụ (Host System)"]
        CVAT_Docker["CVAT Docker Compose Stack<br/>• cvat_server / cvat_worker<br/>• postgres / redis / nuclio"]
        Studio_Daemon["idcheck Studio Server Daemon<br/>PID Daemon (cli.py studio -p 8766)<br/>Multi-threading WSGI Server"]
        Cron_Daemon["Watch Daemon / Background Services<br/>cli.py watch --project-id 3 --interval 30"]
        Local_Disk[("Local Storage<br/>• storage/cvat_frames/<br/>• storage/cvat_job_meta.json<br/>• YAML Configs")]
    end

    Annotator_Client -->|HTTPS / WSS| Nginx
    Reviewer_Client -->|HTTPS / WSS| Nginx

    Nginx -->|Proxy Pass 8766| Studio_Daemon
    Nginx -->|Proxy Pass 8080| CVAT_Docker

    Studio_Daemon <-->|REST API SDK| CVAT_Docker
    Cron_Daemon <-->|REST API SDK| CVAT_Docker
    Studio_Daemon <-->|Read / Write| Local_Disk
```

### Các thông số hạ tầng:
- **Tên miền Studio**: `https://studio.trantuli.id.vn`
- **Tên miền CVAT**: `https://cvat.trantuli.id.vn`
- **Cổng nội bộ Studio**: `8766`
- **Quản lý tiến trình nền**: Được giám sát qua Background Process / Systemd Service, đảm bảo tự động phục hồi khi gặp sự cố.
