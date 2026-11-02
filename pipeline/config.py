"""Cấu hình chung: mọi giá trị nằm trong file này (không đọc .env hay biến môi trường)."""
from __future__ import annotations

import re
from pathlib import Path


# 1. Đường dẫn

ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT / "work"          # mỗi hóa đơn một thư mục work/<doc_id>/
LOG_FILE = ROOT / "logs.txt"      # báo cáo append cho mọi hóa đơn
IDS_FILE = ROOT / "ids.txt"       # danh sách id cho run_batch.py


# 2. API

X_BBOX_SESSION = ""               # dán giá trị header x-bbox-session vào đây
BASE_URL = "https://invoice-review-api.lehoangtung7629.workers.dev"
SESSION_HEADER = "x-bbox-session"
HTTP_TIMEOUT = 60                 # giây
HTTP_RETRIES = 3                  # thử lại khi lỗi mạng / 429 / 5xx


# 3. Render

RENDER_DPI = 200
RESPECT_PDF_ROTATE = True         # áp cờ /Rotate như trình xem PDF; False nếu backend bỏ qua /Rotate


# 4. OCR và bản nháp

OCR_LANG = "vie+eng"
TESSERACT_CMD = ""                # đường dẫn tesseract.exe nếu không có trong PATH, vd r"C:\Program Files\Tesseract-OCR\tesseract.exe"
OCR_PSM_PAGE = 3                  # Tesseract: phân tích bố cục tự động cho cả trang
OCR_PSM_REGION = 6                # Tesseract: một khối chữ, dùng cho ocr-region
OCR_REGION_SCALE = 3              # ocr-region phóng to vùng trước khi OCR
DRAFT_MIN_SCORE = 0.9             # ứng viên đạt điểm này mới vào bản nháp


# 5. Bbox

PAD_PX = 5
COORD_DIGITS = 4                  # số chữ số thập phân khi gửi tọa độ chuẩn hóa


# 6. Ảnh cho Claude xem: token = ceil(rộng/28) × ceil(cao/28), tối đa 4.784

THUMB_LONG_EDGE = 800             # thumbnail xác định orientation (~600 token)
OVERLAY_LONG_EDGE = 1300          # ảnh phủ box toàn trang (~1.500 token)
CROP_TARGET_TEXT_PX = 48          # phóng đại crop để chữ cao ~48 pixel
CROP_MIN_SCALE = 2.0              # hệ số phóng đại tối thiểu / tối đa cho crop
CROP_MAX_SCALE = 5.0
CROP_FLOOR_SCALE = 1.5            # crop quá dài được giảm tới mức này để vừa sheet
SHEET_WIDTH = 1904                # 68 ô × 28 pixel
SHEET_MAX_HEIGHT = 1960           # 70 ô × 28 pixel -> ≤ 4.760 token


# 7. Review

MAX_REVIEW_ROUNDS = 3             # vòng 1 xem mọi box; vòng 2–3 chỉ xem box đã sửa
ZOOM_SCALE = 3                    # lệnh zoom: hệ số phóng to
ZOOM_GRID = 50                    # lệnh zoom: khoảng cách vạch lưới (pixel ảnh thẳng)


# 8. run_batch.py

BATCH_MAX_TURNS = 150             # số lượt tối đa của một phiên Claude
BATCH_TIMEOUT = 1800              # giây cho mỗi hóa đơn
BATCH_MODEL = "claude-sonnet-5-5" # mặc định opus
BATCH_EFFORT = "medium"                 # mặc định medium  
CLAUDE_BIN = ""                   # đường dẫn chương trình claude nếu không tự tìm được


# 9. Trạng thái và no_box

# verified -> gửi "box"; no_box -> gửi "no_box" (kèm lý do); needs_review -> không gửi
NO_BOX_STATUS = "no_box"
VALID_STATUSES = {"verified", "no_box", "needs_review"}
NO_BOX_REASONS = {
    "null_value": "biểu mẫu để null",
    "not_found": "không tìm thấy trên hóa đơn sau khi đã tìm kỹ",
    "zero_other_form": "giá trị bằng 0 nhưng in dạng khác (vd KCT = không chịu thuế)",
}


def no_box_payload(field_key: str) -> dict:
    """Request PUT cho action no_box. Nếu backend cần thêm trường, sửa tại đây."""
    return {"field_key": field_key, "action": "no_box"}



# 10. Trường cần định vị

INVOICE_KEY_TEMPLATE = "invoices[{i}].{path}"
ITEM_KEY_TEMPLATE = "invoices[{i}].items[{j}].{key}"   # kiểm tra lại với backend nếu khác

INVOICE_FIELDS = [
    ("supplier.supplier_name", "supplier_name", "Tên nhà cung cấp", "#dc2626"),
    ("supplier.tax_code", "supplier_tax_code", "MST bên bán", "#e11d48"),
    ("invoice.invoice_number", "invoice_number", "Số hóa đơn", "#7c3aed"),
    ("invoice.invoice_serial", "invoice_serial", "Ký hiệu", "#0891b2"),
    ("invoice.invoice_date", "invoice_date", "Ngày hóa đơn", "#be185d"),
    ("invoice.vat_rate_percent", "vat_rate_percent", "VAT", "#2563eb"),
]
ITEM_FIELDS = [
    ("medicine_name_strength_manufacturer_country", "Tên thuốc / hàm lượng / hãng / nước", "#c2410c"),
    ("unit", "Đơn vị", "#a21caf"),
    ("quantity", "Số lượng", "#0284c7"),
    ("unit_price", "Đơn giá", "#0d9488"),
    ("line_amount", "Thành tiền", "#a16207"),
    ("vat_amount", "Tiền VAT", "#4338ca"),
    ("lot_number", "Số lô", "#475569"),
    ("expiry_date", "Hạn dùng", "#b45309"),
]
TOTAL_FIELDS = [
    ("totals.grand_total", "grand_total", "Tổng giá trị hóa đơn", "#4d7c0f"),
]


# Tiện ích

_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def check_doc_id(doc_id: str) -> str:
    """Chặn id lạ (khoảng trắng, '/', '..') để không ghi file ra ngoài work/."""
    doc_id = str(doc_id).strip()
    if not _DOC_ID_RE.match(doc_id) or doc_id in {".", ".."}:
        raise ValueError(f"doc_id không hợp lệ: {doc_id!r}")
    return doc_id


def doc_dir(doc_id: str) -> Path:
    return WORK_DIR / check_doc_id(doc_id)


def load_session() -> str:
    return X_BBOX_SESSION.strip()