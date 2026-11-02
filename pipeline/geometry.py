"""Quy đổi tọa độ và tiện ích box.

ảnh gốc --quy tắc xoay--> ảnh thẳng (pixel) --chuẩn hóa + quay--> hệ backend (0–1)
  landscape: ảnh xoay 90° ngược chiều kim đồng hồ; tọa độ backend = quay 180°
  portrait : ảnh giữ nguyên;                       tọa độ backend = quay 90° cùng chiều kim đồng hồ
"""
from __future__ import annotations

ORIENTATIONS = ("landscape", "portrait")


def upright_to_backend(x: float, y: float, orientation: str) -> tuple[float, float]:
    """Ảnh thẳng (0–1) -> backend. Landscape: (1-x, 1-y); portrait: (1-y, x)."""
    if orientation == "landscape":
        return 1.0 - x, 1.0 - y
    if orientation == "portrait":
        return 1.0 - y, x
    raise ValueError(orientation)


def upright_box_to_backend_points(box, orientation: str, upright_width: int, upright_height: int,
                                  ndigits: int = 4) -> list[dict]:
    """Box pixel ảnh thẳng -> 4 điểm backend: trên-trái, trên-phải, dưới-phải, dưới-trái."""
    x1, y1, x2, y2 = box
    w, h = upright_width, upright_height
    corners = [(x1 / w, y1 / h), (x2 / w, y1 / h), (x2 / w, y2 / h), (x1 / w, y2 / h)]
    mapped = [upright_to_backend(x, y, orientation) for x, y in corners]
    xs = [min(1.0, max(0.0, p[0])) for p in mapped]
    ys = [min(1.0, max(0.0, p[1])) for p in mapped]
    left, right = round(min(xs), ndigits), round(max(xs), ndigits)
    top, bottom = round(min(ys), ndigits), round(max(ys), ndigits)
    return [{"x": left, "y": top}, {"x": right, "y": top},
            {"x": right, "y": bottom}, {"x": left, "y": bottom}]


def area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def intersection(a, b) -> float:
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def iou(a, b) -> float:
    """Tỷ lệ giao trên hợp của hai box."""
    inter = intersection(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def union_box(boxes) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]
