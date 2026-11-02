"""Render PDF thành ảnh gốc, tạo ảnh thẳng và thumbnail."""
from __future__ import annotations

from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # bản cũ
    import fitz
from PIL import Image

from . import config

Image.MAX_IMAGE_PIXELS = None


def render_pdf(pdf_path: Path, out_dir: Path, dpi: int = config.RENDER_DPI) -> list[dict]:
    """Render từng trang thành ảnh gốc pages/page_XXX_raw.png (không sửa PDF)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    zoom = dpi / 72.0
    pages = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            pdf_rotate = page.rotation
            if not config.RESPECT_PDF_ROTATE and pdf_rotate:
                page.set_rotation(0)          # chỉ trong bộ nhớ, không lưu lại PDF
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            path = out_dir / f"page_{i:03d}_raw.png"
            pix.save(path)
            pages.append({
                "page": i, "raw_width": pix.width, "raw_height": pix.height, "dpi": dpi,
                "pdf_rotate": pdf_rotate, "respect_pdf_rotate": config.RESPECT_PDF_ROTATE,
                "pdf_page_size": [page.rect.width, page.rect.height],
                "raw_image": str(path.relative_to(out_dir.parent)),
            })
    return pages


def make_upright(raw_path: Path, orientation: str, upright_path: Path) -> tuple[int, int]:
    """Ảnh thẳng: landscape xoay 90° ngược chiều kim đồng hồ, portrait giữ nguyên."""
    with Image.open(raw_path) as im:
        if orientation == "landscape":
            out = im.transpose(Image.Transpose.ROTATE_90)    # ROTATE_90 của PIL = ngược chiều kim đồng hồ
        elif orientation == "portrait":
            out = im.copy()
        else:
            raise ValueError(orientation)
        out.save(upright_path)
        return out.size


def make_thumbnail(src: Path, dst: Path, long_edge: int = config.THUMB_LONG_EDGE) -> tuple[int, int]:
    """Ảnh thu nhỏ để nhìn hướng chữ; tọa độ vẫn tính trên ảnh đầy đủ."""
    with Image.open(src) as im:
        s = long_edge / max(im.size)
        out = im.convert("RGB")
        if s < 1:
            out = out.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        out.save(dst)
        return out.size
