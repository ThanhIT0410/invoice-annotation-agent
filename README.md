# Invoice Annotation Agent

Agent dựa trên Claude Code, định vị bounding box (bbox) cho các trường trên hóa đơn PDF scan rồi gửi kết quả lên backend invoice-review-api.

- **Claude** xác định hướng trang, chọn đúng chữ và ngữ cảnh, xem crop phóng đại để quyết định box có đạt không.
- **Python và Tesseract OCR** lo phần tất định: tải dữ liệu, render, xoay ảnh, lấy tọa độ chữ, quy đổi tọa độ, gọi API và ghi log.

Quy trình xử lý chi tiết nằm trong [SOP_pipeline_claude_bbox.md](SOP_pipeline_claude_bbox.md).

## Cài đặt

Hướng dẫn dưới đây dành cho Windows và dùng PowerShell.

### 1. Tải project và cài thư viện Python

Máy cần có sẵn [Git](https://git-scm.com/download/win) và [Python 3](https://www.python.org/downloads/).

```powershell
git clone https://github.com/ThanhIT0410/invoice-annotation-agent.git
cd invoice-annotation-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Cài Claude Code

```powershell
irm https://claude.ai/install.ps1 | iex
```

Mở lại terminal, chạy `claude` và đăng nhập theo hướng dẫn trên màn hình.

Nếu muốn dùng trong VS Code, cài thêm tiện ích **Claude Code** từ Extensions.

### 3. Cài Tesseract OCR có tiếng Việt

1. Tải bộ cài từ [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) và chạy.
2. Ở bước **Choose Components**, mở **Additional language data (download)** và tick **Vietnamese**.
3. Kiểm tra sau khi cài, trong danh sách phải có `vie` và `eng`:

   ```powershell
   & "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
   ```

4. Nếu `tesseract` chưa có trong PATH, khai báo đường dẫn trong [pipeline/config.py](pipeline/config.py):

   ```python
   TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
   ```

### 4. Lấy `x-bbox-session`

Gọi API login bằng tài khoản invoice-review-api của bạn:

```
https://invoice-review-api.lehoangtung7629.workers.dev/api/bbox/login
```

Nếu đăng nhập thành công, sao chép giá trị session trong kết quả và dán vào [pipeline/config.py](pipeline/config.py):

```python
X_BBOX_SESSION = "giá-trị-session"
```

## Cấu hình khác

Mọi cấu hình nằm trong [pipeline/config.py](pipeline/config.py). Project không đọc file `.env`, dù một số thông báo lỗi và tài liệu cũ vẫn nhắc đến file này.

| Biến | Ý nghĩa |
|---|---|
| `BASE_URL` | Địa chỉ gốc của API |
| `CLAUDE_BIN` | Đường dẫn chương trình `claude`, chỉ cần khi `run_batch.py` không tự tìm được |
| `BATCH_MODEL`, `BATCH_EFFORT` | Model và mức effort khi chạy hàng loạt |
| `BATCH_MAX_TURNS`, `BATCH_TIMEOUT` | Giới hạn số lượt và thời gian (giây) cho mỗi hóa đơn |

## Sử dụng

Cả hai cách dưới đây đều **gửi bbox thật lên backend** và dùng hạn mức Claude của bạn.

### Xử lý một hóa đơn

Mở thư mục project trong Claude Code rồi gõ:

```text
Xử lý hóa đơn 202606241434
```

Hoặc dùng lệnh `/xu-ly-hoa-don 202606241434`. Agent sẽ tự làm hết các bước và tóm tắt kết quả khi xong.

### Xử lý hàng loạt

1. Tạo file `ids.txt` ở thư mục gốc. Mỗi dòng một id, có thể ghi chú sau dấu `#`:

   ```text
   202606241444-1
   202606241445  # hóa đơn cần ưu tiên
   ```

2. Chạy:

   ```powershell
   python run_batch.py
   ```

Mỗi hóa đơn chạy trong một phiên Claude riêng. Hóa đơn đã hoàn thành sẽ được bỏ qua, nên có thể chạy lại lệnh này sau khi bị gián đoạn.

| Kết quả | Ý nghĩa |
|---|---|
| `COMPLETE` | Đã xử lý xong |
| `INCOMPLETE` | Đã có báo cáo nhưng chưa hoàn tất, và sẽ được chạy lại ở lần sau |
| `ABORTED` | Agent dừng và đã ghi lý do |
| `NO_REPORT` | Phiên kết thúc mà không có báo cáo, ví dụ quá thời gian. `run_batch.py` tự ghi lại lỗi |

## Kết quả

- **`logs.txt`:** báo cáo của mọi hóa đơn, chỉ ghi nối thêm và không nên xóa.
- **`work/<id>/`:** dữ liệu làm việc của từng hóa đơn, gồm ảnh trang, `audit.json` và các file trạng thái `DONE` hoặc `ABORTED`.
- **`work/<id>/claude_session.json`:** số lượt, thời gian và chi phí của phiên khi chạy hàng loạt.

`work/`, `logs.txt` và `ids.txt` nằm trong `.gitignore` nên không được đưa lên repo.

## Cấu trúc thư mục

```text
.claude/skills/xu-ly-hoa-don/   Quy trình từng bước cho agent
pipeline/agent.py               Các lệnh agent gọi
pipeline/api.py                 Gọi API backend
pipeline/config.py              Cấu hình
pipeline/geometry.py            Quy đổi tọa độ
pipeline/match.py               Khớp giá trị biểu mẫu với chữ OCR
pipeline/render.py              Render PDF và xoay ảnh
pipeline/report.py              Ghi báo cáo
pipeline/verify.py              Tự kiểm tra, tinh chỉnh box, tạo crop
pipeline/words.py               OCR ra chữ kèm tọa độ
run_batch.py                    Chạy hàng loạt
CLAUDE.md                       Luật chung cho Claude Code
SOP_pipeline_claude_bbox.md     SOP đầy đủ
```
