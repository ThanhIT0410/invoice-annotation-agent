"""OCR Tesseract trên ảnh thẳng -> word kèm bbox pixel (không đổi giá trị biểu mẫu)."""
from __future__ import annotations

import statistics
from pathlib import Path

from PIL import Image

from . import config


def ocr_words(img: Image.Image, offset=(0.0, 0.0), scale: float = 1.0,
              lang: str = config.OCR_LANG, psm: int = 3) -> list[dict]:
    """OCR mức word; offset/scale quy tọa độ crop phóng to về ảnh gốc."""
    import pytesseract
    from pytesseract import Output

    if config.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD

    data = pytesseract.image_to_data(img, lang=lang, config=f"--psm {psm}",
                                     output_type=Output.DICT)
    out = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        conf = float(data["conf"][i])
        if not text or conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        out.append({
            "text": text,
            "bbox": [x / scale + offset[0], y / scale + offset[1],
                     (x + w) / scale + offset[0], (y + h) / scale + offset[1]],
            "conf": conf, "source": "ocr",
        })
    return out


def group_lines(words: list[dict]) -> list[dict]:
    """Gom word thành dòng theo tâm y, sắp trái->phải; gán line_id và idx (thứ tự đọc)."""
    if not words:
        return []
    heights = [w["bbox"][3] - w["bbox"][1] for w in words]
    tol = max(4.0, 0.5 * statistics.median(heights))
    ws = sorted(words, key=lambda w: (w["bbox"][1] + w["bbox"][3]) / 2)
    lines: list[list[dict]] = []
    centers: list[float] = []
    for w in ws:
        yc = (w["bbox"][1] + w["bbox"][3]) / 2
        if centers and abs(yc - centers[-1]) <= tol:
            lines[-1].append(w)
            n = len(lines[-1])
            centers[-1] = centers[-1] + (yc - centers[-1]) / n
        else:
            lines.append([w])
            centers.append(yc)
    out = []
    for lid, line in enumerate(lines):
        for w in sorted(line, key=lambda w: w["bbox"][0]):
            out.append({**w, "line": lid, "idx": len(out)})
    return out
