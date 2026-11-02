# Invoice BBox Agent

Dự án định vị bbox cho hóa đơn trên invoice-review-api. Claude chọn đúng chữ / đúng ngữ cảnh;
OCR (hóa đơn là PDF scan) cung cấp hình học; crop phóng đại quyết định box có được chấp nhận.

## Kích hoạt
- Khi người dùng gõ **"Xử lý hóa đơn <id>"** (hoặc `/xu-ly-hoa-don <id>`): dùng skill `xu-ly-hoa-don`
  (`.claude/skills/xu-ly-hoa-don/SKILL.md`) và làm đủ các bước, không hỏi lại giữa chừng.
- Mỗi lần chỉ xử lý MỘT hóa đơn. Xử lý hàng loạt do `run_batch.py` mở mỗi hóa đơn một phiên mới.

## Luật chung
- Công cụ: `.venv/Scripts/python -m pipeline.agent <lệnh> <id>` — dùng đúng lệnh này, không chạy `-h`, không kiểm tra `.venv`.
- Không sửa giá trị biểu mẫu; sai lệch chỉ báo cáo trong logs.txt.
- Không đọc/in `.env` (chứa x-bbox-session).
- Không xoá `logs.txt`; chỉ append qua lệnh `report` / `abort`.
- Quy tắc xoay cố định: landscape → xoay ảnh 90° ngược chiều kim đồng hồ, bbox quay 180° khi gửi;
  portrait → không xoay ảnh, bbox quay 90° cùng chiều kim đồng hồ khi gửi. Quy tắc không làm hóa đơn thẳng → `abort` và dừng.
- Chạy không người giám sát (`claude -p`): không đặt câu hỏi; tự quyết theo SOP và ghi rõ vào audit.
