"""Client cho invoice-review-api: tải PDF, lấy biểu mẫu, PUT từng bbox."""
from __future__ import annotations

import time
from pathlib import Path

import requests

from . import config


class ApiError(RuntimeError):
    """Lỗi gọi API (thiếu session, HTTP lỗi, mạng hỏng sau khi đã retry)."""


def _headers(json_body: bool = False) -> dict:
    """Header bắt buộc: x-bbox-session (người dùng tự dán vào .env)."""
    session = config.load_session()
    if not session:
        raise ApiError(
            "Chưa có x-bbox-session. Dán vào file .env dòng X_BBOX_SESSION=... "
            "hoặc đặt biến môi trường X_BBOX_SESSION."
        )
    h = {config.SESSION_HEADER: session, "Accept": "application/json, application/pdf, */*"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _request(method: str, path: str, **kwargs) -> requests.Response:
    """Gọi HTTP có retry với lỗi mạng, 408/429/5xx (backoff 2, 4, 8 giây)."""
    url = f"{config.BASE_URL}{path}"
    last = "unknown"
    for attempt in range(1, config.HTTP_RETRIES + 1):
        try:
            r = requests.request(method, url, timeout=config.HTTP_TIMEOUT, **kwargs)
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        else:
            if r.status_code < 400:
                return r
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in (408, 429, 500, 502, 503, 504):
                break
        if attempt < config.HTTP_RETRIES:
            time.sleep(2 ** attempt)
    raise ApiError(f"{method} {path} thất bại: {last}")


def get_document(doc_id: str) -> dict:
    """GET /api/documents/{doc_id} -> biểu mẫu hóa đơn đã điền sẵn (JSON)."""
    r = _request("GET", f"/api/documents/{doc_id}", headers=_headers())
    return r.json()


def download_pdf(doc_id: str, dest: Path) -> Path:
    """GET /api/documents/{doc_id}/pdf -> lưu nguyên byte vào dest."""
    r = _request("GET", f"/api/documents/{doc_id}/pdf", headers=_headers())
    if not r.content.startswith(b"%PDF"):
        raise ApiError(f"Nội dung tải về không phải PDF (đầu file: {r.content[:20]!r})")
    dest.write_bytes(r.content)
    return dest


def put_box(doc_id: str, payload: dict) -> tuple[int, str]:
    """PUT /api/bbox/documents/{doc_id} cho MỘT bbox. Trả (status_code, body rút gọn)."""
    r = _request("PUT", f"/api/bbox/documents/{doc_id}", headers=_headers(True), json=payload)
    return r.status_code, r.text[:500]


ADMIN_BEARER = "123"


def tag_ai_source(doc_id: str) -> tuple[int, str]:
    """PATCH /api/admin/documents/{doc_id} body {"bbox_source": "ai"}. Trả (status_code, body rút gọn)."""
    headers = {**_headers(True), "Authorization": f"Bearer {ADMIN_BEARER}"}
    r = _request("PATCH", f"/api/admin/documents/{doc_id}", headers=headers, json={"bbox_source": "ai"})
    return r.status_code, r.text[:500]

