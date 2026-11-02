"""QA hình học bbox theo nét mực và ảnh bằng chứng cho Claude xem."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import config, geometry

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def font(size: int = 16):
    """Font có dấu tiếng Việt để ghi nhãn lên ảnh; không có thì dùng font mặc định."""
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def load_gray(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L"))


# ------------------------------------------------------------------ mặt nạ mực
def otsu(gray: np.ndarray) -> int:
    """Ngưỡng Otsu tách nền / mực."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = gray.size
    sum_all = float(np.dot(np.arange(256), hist))
    w_b = sum_b = best = 0.0
    thr = 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, thr = between, t
    return thr


def ink_mask(gray: np.ndarray) -> np.ndarray:
    """True ở pixel mực. Vùng gần như đồng màu -> không có mực.
    Có watermark (mực nhạt hơn hẳn chữ) -> chỉ lấy phần đậm là chữ."""
    if gray.size == 0 or gray.std() < 8:
        return np.zeros_like(gray, dtype=bool)
    thr = min(otsu(gray), 190)
    dark = gray[gray <= thr]
    if dark.size >= 50:
        t2 = otsu(dark)
        lo, hi = dark[dark <= t2], dark[dark > t2]
        # hai lớp mực: chữ đậm (< 110) và lớp nhạt chiếm nhiều (watermark) -> bỏ lớp nhạt
        if lo.size and hi.size and lo.mean() < 110 and hi.mean() - lo.mean() >= 60 and hi.size >= lo.size:
            thr = t2
    return gray <= thr


LINE_MIN = 40     # vệt mực liền dài ≥ 40 pixel (và ≥ 1/4 vùng xét) là đường kẻ; nét chữ không dài vậy


def _long_runs(mask: np.ndarray, min_len: int) -> np.ndarray:
    """Pixel thuộc vệt ngang liền (hở ≤ 2 pixel) dài ≥ min_len — bắt được cả border nghiêng từng khúc.
    Khúc ngắn ở mép vùng xét, dính trên/dưới một vệt dài, là đuôi của border nghiêng -> cũng là đường kẻ."""
    h, w = mask.shape
    out = np.zeros_like(mask)
    tails = []
    for r in range(h):
        idx = np.flatnonzero(mask[r])
        if idx.size == 0:
            continue
        breaks = np.flatnonzero(np.diff(idx) > 2)
        for a, b in zip(np.r_[idx[0], idx[breaks + 1]], np.r_[idx[breaks], idx[-1]]):
            if b - a + 1 >= min_len:
                out[r, a:b + 1] = mask[r, a:b + 1]
            elif b - a + 1 >= 8 and (a <= 2 or b >= w - 3):
                tails.append((r, a, b))
    for r, a, b in tails:
        if (r > 0 and out[r - 1, a:b + 1].any()) or (r + 1 < h and out[r + 1, a:b + 1].any()):
            out[r, a:b + 1] = mask[r, a:b + 1]
    return out


def _erase(m: np.ndarray, line: np.ndarray, text: np.ndarray) -> None:
    """Xóa pixel đường kẻ theo từng cột; đoạn nào có nét chữ dính ngay trên/dưới thì giữ
    (chữ chạm / đè border) — xóa luôn sẽ làm box co lẹm vào chữ."""
    h = m.shape[0]
    for c in np.flatnonzero(line.any(0)):
        col = line[:, c]
        idx = np.flatnonzero(col)
        breaks = np.flatnonzero(np.diff(idx) > 1)
        for a, b in zip(np.r_[idx[0], idx[breaks + 1]], np.r_[idx[breaks], idx[-1]]):
            # viền 1 pixel lởm chởm ở mép đường kẻ (bản scan, watermark đè) thuộc về đường kẻ
            if a > 0 and text[a - 1, c] and not (a > 1 and text[a - 2, c]):
                a -= 1
            if b + 1 < h and text[b + 1, c] and not (b + 2 < h and text[b + 2, c]):
                b += 1
            # nét chữ dính vào phải sâu ≥ 2 pixel
            if not ((a > 1 and text[a - 2:a, c].all()) or (b + 2 < h and text[b + 1:b + 3, c].all())):
                m[a:b + 1, c] = False


DOT_GAP = 12      # chấm cách nhau ≤ 12 pixel coi như liền
DOT_BAND = 5      # đường chấm dày tối đa 5 pixel (dòng chữ dày hơn nhiều)
DOT_ALONE = 0.1   # ≤ 10% cột của dải được có mực ngay sát trên/dưới (đáy/đỉnh chữ thì có nhiều)
DOT_MIN = 8       # tối thiểu 8 chấm


def _dotted(mask: np.ndarray, frac: float) -> np.ndarray:
    """Vị trí hàng là đường chấm: nối khoảng hở ≤ DOT_GAP thì phủ ≥ frac bề rộng,
    dải mỏng ≤ DOT_BAND, ≥ DOT_MIN chấm và tách biệt (sát trên/dưới gần như không có mực)."""
    h, w = mask.shape
    ok = np.zeros(h, bool)
    for r in range(h):
        idx = np.flatnonzero(mask[r])
        if idx.size < 2:
            continue
        gaps = np.diff(idx) - 1
        ok[r] = (idx[-1] - idx[0] + 1 - gaps[gaps > DOT_GAP].sum()) >= frac * w
    out = np.zeros(h, bool)
    r = 0
    while r < h:  # chỉ giữ dải mỏng; dải dày là dòng chữ
        if ok[r]:
            e = r
            while e + 1 < h and ok[e + 1]:
                e += 1
            cols = mask[r:e + 1].any(0)
            a, z = r, e  # nới dải sang hàng sát bên chỉ chứa phần của các chấm (chấm cao thấp không đều)
            while a > 0 and mask[a - 1].any() and not (mask[a - 1] & ~cols).any():
                a -= 1
            while z + 1 < h and mask[z + 1].any() and not (mask[z + 1] & ~cols).any():
                z += 1
            near = mask[max(0, a - 2):a].any(0) | mask[z + 1:z + 3].any(0)
            dots = int(cols[0]) + int((np.diff(cols.astype(np.int8)) == 1).sum())  # số đoạn mực rời
            if z - a + 1 <= DOT_BAND and dots >= DOT_MIN and (near & cols).sum() <= DOT_ALONE * cols.sum():
                out[a:z + 1] = True
            r = e + 1
        else:
            r += 1
    return out


def remove_lines(mask: np.ndarray) -> tuple[np.ndarray, dict]:
    """Bỏ đường kẻ bảng (liền, chấm, dày, hơi nghiêng); giữ nét chữ chạm vào đường kẻ."""
    h, w = mask.shape
    hl = _long_runs(mask, max(LINE_MIN, w // 4)) if w >= 60 else np.zeros_like(mask)
    vl = _long_runs(mask.T, max(LINE_MIN, h // 4)).T if h >= 30 else np.zeros_like(mask)
    dot = _dotted(mask, 0.6) if w >= 60 else np.zeros(h, bool)
    hl[dot] = mask[dot]
    dot = _dotted(mask.T, 0.9) if h >= 30 else np.zeros(w, bool)
    vl[:, dot] = mask[:, dot]
    text = mask & ~hl & ~vl
    m = mask.copy()
    _erase(m, hl, text)
    _erase(m.T, vl.T, text.T)
    rows = np.flatnonzero(hl.sum(1) >= 0.25 * w)
    cols = np.flatnonzero(vl.sum(0) >= 0.25 * h)
    return m, {"rows": rows.tolist(), "cols": cols.tolist()}


def _region(gray: np.ndarray, bbox, margin: int | None = None):
    """Cắt box + lề ngữ cảnh; trả vùng, mặt nạ mực (đã bỏ đường kẻ), box trong vùng, gốc."""
    H, W = gray.shape
    x1, y1, x2, y2 = [int(round(v)) for v in bbox]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W, max(x2, x1 + 1)), min(H, max(y2, y1 + 1))
    h = y2 - y1
    m = margin if margin is not None else int(max(8, 0.6 * h))
    ex1, ey1, ex2, ey2 = max(0, x1 - m), max(0, y1 - m), min(W, x2 + m), min(H, y2 + m)
    sub = gray[ey1:ey2, ex1:ex2]
    mask, lines = remove_lines(ink_mask(sub))
    return sub, mask, lines, (x1 - ex1, y1 - ey1, x2 - ex1, y2 - ey1), (ex1, ey1)


def _edge_cut(mask, bx1, by1, bx2, by2) -> dict:
    """Cạnh nào có nét mực vắt qua (mực ở cả mép trong lẫn ngay ngoài box) -> bị cắt."""
    h, w = mask.shape
    cut = {}
    if bx1 - 1 >= 0:
        cut["left"] = int((mask[by1:by2, bx1] & mask[by1:by2, bx1 - 1]).sum())
    if bx2 < w:
        cut["right"] = int((mask[by1:by2, bx2 - 1] & mask[by1:by2, bx2]).sum())
    if by1 - 1 >= 0:
        cut["top"] = int((mask[by1, bx1:bx2] & mask[by1 - 1, bx1:bx2]).sum())
    if by2 < h:
        cut["bottom"] = int((mask[by2 - 1, bx1:bx2] & mask[by2, bx1:bx2]).sum())
    return {k: v for k, v in cut.items() if v >= 2}


def _gaps(profile: np.ndarray, min_gap: float) -> list[int]:
    """Độ dài các khoảng trống (giữa hai vùng có mực) dài hơn min_gap."""
    idx = np.where(profile)[0]
    if idx.size < 2:
        return []
    d = np.diff(idx) - 1
    return [int(g) for g in d if g > min_gap]


def _line_height(inner: np.ndarray) -> int:
    """Cao 1 dòng chữ = đoạn mực dọc liên tục dài nhất."""
    runs, cur = [], 0
    for v in inner.any(1):
        cur = cur + 1 if v else (runs.append(cur) or 0)
    runs.append(cur)
    return max(max(runs), 4)


def analyze_box(gray: np.ndarray, bbox) -> dict:
    """Đo padding từng cạnh, phát hiện cắt lẹm, chứa nội dung thừa, dính đường kẻ, box rỗng."""
    sub, mask, lines, (bx1, by1, bx2, by2), origin = _region(gray, bbox)
    inner = mask[by1:by2, bx1:bx2]
    issues: list[dict] = []
    if not inner.any():
        return {"ok": False, "issues": [{"code": "empty", "msg": "box không chứa nét chữ nào"}],
                "metrics": {}}
    rows = np.where(inner.any(1))[0]
    cols = np.where(inner.any(0))[0]
    iy1, iy2, ix1, ix2 = int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])
    text_h = iy2 - iy1 + 1
    pads = {"left": ix1, "top": iy1,
            "right": (bx2 - bx1 - 1) - ix2, "bottom": (by2 - by1 - 1) - iy2}

    line_h = _line_height(inner)
    runs, cur = [], 0
    for v in inner.any(1):
        cur = cur + 1 if v else (runs.append(cur) or 0)
    runs.append(cur)
    n_lines = sum(1 for r in runs if r >= 0.4 * line_h)

    cut = _edge_cut(mask, bx1, by1, bx2, by2)
    for side in cut:
        issues.append({"code": f"cut_{side}",
                       "msg": f"nét chữ vắt qua cạnh {_vn(side)} — cắt lẹm (vào chính nội dung hoặc nội dung lân cận)"})
    # nét chữ ngay ngoài cạnh (khe < 0,3 × cao chữ): box có thể cắt thiếu ký tự
    near_gap = max(2, int(0.3 * line_h))
    band = mask[by1 + iy1: by1 + iy2 + 1]
    for side in ("left", "right"):
        if side in cut:
            continue
        if side == "right":
            strip = band[:, bx2: min(mask.shape[1], bx2 + near_gap + 1)]
            hit = np.where(strip.any(0))[0]
            dist = int(hit[0]) if hit.size else None
        else:
            strip = band[:, max(0, bx1 - near_gap - 1): bx1]
            hit = np.where(strip.any(0))[0]
            dist = int(strip.shape[1] - 1 - hit[-1]) if hit.size else None
        if dist is not None:
            issues.append({"code": f"near_{side}",
                           "msg": f"có nét chữ ngay ngoài cạnh {_vn(side)} (cách {dist} pixel) — "
                                  "có thể cắt thiếu ký tự hoặc sát nội dung lân cận"})
    wide_limit = max(3 * config.PAD_PX, 0.5 * line_h)
    for side, p in pads.items():
        if side in cut:
            continue
        if p > wide_limit:
            issues.append({"code": f"pad_wide_{side}",
                           "msg": f"padding {_vn(side)} quá rộng ({p} pixel, ~{p / line_h:.1f}× cao chữ)"})
        elif p < 1:
            issues.append({"code": f"pad_narrow_{side}",
                           "msg": f"padding {_vn(side)} quá hẹp (nét chữ chạm mép box)"})
    hgaps = _gaps(inner.any(0), 1.5 * line_h)
    if hgaps:
        issues.append({"code": "inner_gap",
                       "msg": f"có khoảng trống lớn trong box ({max(hgaps)} pixel) — có thể box chứa thêm nhãn/giá trị khác"})
    line_rows = [r for r in lines["rows"] if by1 <= r < by2]
    line_cols = [c for c in lines["cols"] if bx1 <= c < bx2]
    if line_rows or line_cols:
        issues.append({"code": "table_line",
                       "msg": "box dính đường kẻ bảng " + ("ngang " if line_rows else "") + ("dọc" if line_cols else "")})
    return {"ok": not issues, "issues": issues,
            "metrics": {"pads": pads, "line_h": line_h, "lines": n_lines,
                        "ink_bbox": [ix1 + bx1 + origin[0], iy1 + by1 + origin[1],
                                     ix2 + bx1 + origin[0] + 1, iy2 + by1 + origin[1] + 1]}}


def _vn(side: str) -> str:
    return {"left": "trái", "right": "phải", "top": "trên", "bottom": "dưới"}[side]


def tighten(gray: np.ndarray, bbox, pad: int = config.PAD_PX) -> tuple[list[float], dict]:
    """Bó box sát nét mực + pad; nới cạnh đang cắt ngang nét chữ."""
    H, W = gray.shape
    sub, mask, _lines, (bx1, by1, bx2, by2), (ox, oy) = _region(gray, bbox)
    h, w = mask.shape
    limit = int(max(8, 0.6 * (by2 - by1)))
    grown = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    changed = True
    while changed:
        changed = False
        if bx1 > 0 and grown["left"] < limit and (mask[by1:by2, bx1] & mask[by1:by2, bx1 - 1]).any():
            bx1 -= 1; grown["left"] += 1; changed = True
        if bx2 < w and grown["right"] < limit and (mask[by1:by2, bx2 - 1] & mask[by1:by2, bx2]).any():
            bx2 += 1; grown["right"] += 1; changed = True
        if by1 > 0 and grown["top"] < limit and (mask[by1, bx1:bx2] & mask[by1 - 1, bx1:bx2]).any():
            by1 -= 1; grown["top"] += 1; changed = True
        if by2 < h and grown["bottom"] < limit and (mask[by2 - 1, bx1:bx2] & mask[by2, bx1:bx2]).any():
            by2 += 1; grown["bottom"] += 1; changed = True
    inner = mask[by1:by2, bx1:bx2]
    if not inner.any():
        return list(bbox), {"changed": False, "reason": "không có nét mực trong box"}
    rows = np.where(inner.any(1))[0]
    cols = np.where(inner.any(0))[0]
    nx1 = max(0, ox + bx1 + int(cols[0]) - pad)
    ny1 = max(0, oy + by1 + int(rows[0]) - pad)
    nx2 = min(W, ox + bx1 + int(cols[-1]) + 1 + pad)
    ny2 = min(H, oy + by1 + int(rows[-1]) + 1 + pad)
    new = [float(nx1), float(ny1), float(nx2), float(ny2)]
    return new, {"changed": new != [float(v) for v in bbox], "grown": grown}


def overlap_issues(boxes: list[dict]) -> dict[str, list[dict]]:
    """Phát hiện box chồng lấn / trùng nhau giữa các field khác nhau trên cùng trang."""
    out: dict[str, list[dict]] = {}
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a["page"] != b["page"]:
                continue
            inter = geometry.intersection(a["bbox"], b["bbox"])
            if inter <= 0:
                continue
            iou = geometry.iou(a["bbox"], b["bbox"])
            code = "duplicate" if iou > 0.9 else "overlap"
            for x, y in ((a, b), (b, a)):
                out.setdefault(x["field_key"], []).append({
                    "code": code,
                    "msg": (f"trùng gần như hoàn toàn với {y['field_key']} (IoU {iou:.2f})" if code == "duplicate"
                            else f"chồng lấn với {y['field_key']} ({inter / max(1.0, geometry.area(x['bbox'])):.0%} diện tích)"),
                })
    return out


# ------------------------------------------------------------------ ảnh bằng chứng
def image_tokens(width: int, height: int) -> int:
    """Số token Claude tính cho một ảnh: ceil(rộng/28) × ceil(cao/28)."""
    return math.ceil(width / 28) * math.ceil(height / 28)


def crop_scale(text_h: float, region_w: int) -> float:
    """Hệ số phóng đại để chữ cao ~CROP_TARGET_TEXT_PX; giảm nếu crop quá dài."""
    s = config.CROP_TARGET_TEXT_PX / max(1.0, text_h)
    s = min(config.CROP_MAX_SCALE, max(config.CROP_MIN_SCALE, s))
    if region_w * s > config.SHEET_WIDTH:
        s = max(config.CROP_FLOOR_SCALE, config.SHEET_WIDTH / region_w)
    return s


def render_crop(img: Image.Image, bbox, color: str, header: list[str],
                text_h: float | None = None) -> Image.Image:
    """Crop box + ngữ cảnh, phóng đại theo chiều cao chữ, vẽ box, tiêu đề gọn."""
    x1, y1, x2, y2 = bbox
    h = max(1.0, y2 - y1)
    c = max(12, int(0.6 * h))
    rx1, ry1 = max(0, int(x1) - c), max(0, int(y1) - c)
    rx2, ry2 = min(img.width, int(x2) + c), min(img.height, int(y2) + c)
    region = img.crop((rx1, ry1, rx2, ry2)).convert("RGB")
    scale = crop_scale(text_h or h, region.width)
    w_out = min(config.SHEET_WIDTH, int(region.width * scale))
    scale = w_out / region.width
    region = region.resize((w_out, max(1, int(region.height * scale))), Image.LANCZOS)
    ImageDraw.Draw(region).rectangle(
        [(x1 - rx1) * scale, (y1 - ry1) * scale, (x2 - rx1) * scale, (y2 - ry1) * scale], outline=color, width=2)
    f = font(15)
    head_h = 19 * len(header) + 4
    text_w = int(max(f.getlength(line) for line in header)) + 8 if header else 0
    out = Image.new("RGB", (min(config.SHEET_WIDTH, max(region.width, text_w)), region.height + head_h), "white")
    hd = ImageDraw.Draw(out)
    for k, line in enumerate(header):
        hd.text((4, 2 + 19 * k), line, fill="black", font=f)
    out.paste(region, (0, head_h))
    return out


def pack_sheets(tiles: list[Image.Image], out_dir: Path, prefix: str, gap: int = 8) -> list[Path]:
    """Xếp crop theo hàng vào sheet dưới trần token, cắt sát nội dung."""
    out_dir.mkdir(parents=True, exist_ok=True)
    order = sorted(range(len(tiles)), key=lambda k: -tiles[k].height)
    sheets: list[list[tuple[int, int, int]]] = []    # mỗi sheet: (tile, x, y)
    cur: list[tuple[int, int, int]] = []
    x = y = row_h = 0
    for k in order:
        t = tiles[k]
        if x and x + t.width > config.SHEET_WIDTH:          # xuống hàng
            x, y, row_h = 0, y + row_h + gap, 0
        if cur and y + t.height > config.SHEET_MAX_HEIGHT:  # sang sheet mới
            sheets.append(cur)
            cur, x, y, row_h = [], 0, 0, 0
        cur.append((k, x, y))
        x += t.width + gap
        row_h = max(row_h, t.height)
    if cur:
        sheets.append(cur)
    paths = []
    for n, placed in enumerate(sheets, start=1):
        w = max(px + tiles[k].width for k, px, _ in placed)
        hgt = max(py + tiles[k].height for k, _, py in placed)
        sheet = Image.new("RGB", (w, hgt), (200, 200, 200))
        for k, px, py in placed:
            sheet.paste(tiles[k], (px, py))
        p = out_dir / f"{prefix}_{n:02d}.png"
        sheet.save(p)
        paths.append(p)
    return paths


def draw_overlay(img: Image.Image, items: list[dict], long_edge: int | None = None) -> Image.Image:
    """Vẽ box đánh số lên ảnh trang; long_edge để thu nhỏ trước."""
    out = img.convert("RGB")
    s = 1.0
    if long_edge and max(out.size) > long_edge:
        s = long_edge / max(out.size)
        out = out.resize((round(out.width * s), round(out.height * s)), Image.LANCZOS)
    else:
        out = out.copy()
    d = ImageDraw.Draw(out)
    f = font(max(13, out.width // 90))
    for it in items:
        if "polygon" in it:
            pts = [(px * s, py * s) for px, py in it["polygon"]]
            d.polygon(pts, outline=it["color"], width=2)
            x, y = pts[0]
        else:
            b = [v * s for v in it["bbox"]]
            d.rectangle(b, outline=it["color"], width=2)
            x, y = b[0], b[1]
        d.text((x, max(0, y - f.size - 1)), str(it["tag"]), fill=it["color"], font=f)
    return out


def ruler_zoom(img: Image.Image, region, scale: float = 3.0, step: int = 50) -> Image.Image:
    """Phóng to một vùng ảnh thẳng kèm lưới tọa độ để định vị bằng mắt."""
    x1, y1, x2, y2 = [int(v) for v in region]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(img.width, x2), min(img.height, y2)
    crop = img.crop((x1, y1, x2, y2)).convert("RGB")
    crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
    d = ImageDraw.Draw(crop)
    f = font(14)
    for gx in range((x1 // step + 1) * step, x2, step):
        X = (gx - x1) * scale
        d.line([(X, 0), (X, crop.height)], fill=(0, 160, 255), width=1)
        d.text((X + 2, 2), str(gx), fill=(0, 100, 200), font=f)
    for gy in range((y1 // step + 1) * step, y2, step):
        Y = (gy - y1) * scale
        d.line([(0, Y), (crop.width, Y)], fill=(255, 120, 0), width=1)
        d.text((2, Y + 2), str(gy), fill=(200, 80, 0), font=f)
    return crop