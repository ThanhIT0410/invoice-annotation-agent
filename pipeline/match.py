"""Khớp giá trị biểu mẫu với word OCR -> bbox ứng viên (Claude chọn occurrence đúng)."""
from __future__ import annotations

import unicodedata
from datetime import date
from difflib import SequenceMatcher

from . import geometry

DATE_KEYS = {"invoice_date", "expiry_date"}
NUMBER_KEYS = {"quantity", "unit_price", "line_amount", "vat_amount", "grand_total"}
PERCENT_KEYS = {"vat_rate_percent"}
MIN_SCORE = 0.8


def norm(s) -> str:
    """Bỏ dấu tiếng Việt, đ->d, chữ thường, bỏ khoảng trắng. Chỉ dùng để so khớp."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D").lower()
    return "".join(s.split())


def _number_variants(v) -> list[str]:
    """15000 -> 15000, 15.000, 15,000, 15.000,00, 15,000.00 ..."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return [str(v)]
    out = []
    if f.is_integer():
        n = int(f)
        us = f"{n:,}"                       # 15,000
        vn = us.replace(",", ".")           # 15.000
        out += [str(n), us, vn, f"{vn},00", f"{us}.00", f"{n},00", f"{n}.00", us.replace(",", " ")]
    else:
        for d in (2, 3, 1):
            s = f"{f:,.{d}f}"               # 1,234.50
            vn = s.replace(",", "_").replace(".", ",").replace("_", ".")
            out += [s, vn, s.replace(",", ""), vn.replace(".", "")]
        out.append(str(v))
    return out


def _date_variants(v) -> list[str]:
    """2026-03-26 -> 26/03/2026, 26/3/2026, 26-03-2026, Ngày 26 tháng 03 năm 2026 ..."""
    try:
        d = date.fromisoformat(str(v)[:10])
    except ValueError:
        return [str(v)]
    dd, mm, yy = f"{d.day:02d}", f"{d.month:02d}", str(d.year)
    out = []
    for D in {dd, str(d.day)}:
        for M in {mm, str(d.month)}:
            for sep in ("/", "-", "."):
                out += [f"{D}{sep}{M}{sep}{yy}", f"{D}{sep}{M}{sep}{yy[2:]}"]
            out.append(f"Ngày {D} tháng {M} năm {yy}")
            out.append(f"{M}/{yy}")         # hạn dùng chỉ có tháng/năm
    out += [str(v), f"{yy}{mm}{dd}", f"{dd}{mm}{yy}"]
    return out


def value_variants(key: str, value) -> list[str]:
    """Các cách giá trị có thể xuất hiện trên hóa đơn, đã chuẩn hóa và bỏ trùng."""
    if key in DATE_KEYS:
        raw = _date_variants(value)
    elif key in NUMBER_KEYS:
        raw = _number_variants(value)
    elif key in PERCENT_KEYS:
        n = _number_variants(value)
        raw = [f"{x}%" for x in n]
    else:
        raw = [str(value)]
    seen, out = set(), []
    for r in raw:
        nr = norm(r)
        if nr and nr not in seen:
            seen.add(nr)
            out.append(nr)
    return out


def _ratio(a: str, b: str) -> float:
    sm = SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < MIN_SCORE or sm.quick_ratio() < MIN_SCORE:
        return 0.0
    return sm.ratio()


_NUM_CHARS = set("0123456789.,")


def _bounded(key: str, token: str, v: str) -> bool:
    """Số/ngày/phần trăm khớp trong token chỉ khi không dính liền chữ số khác."""
    if key not in NUMBER_KEYS and key not in PERCENT_KEYS and key not in DATE_KEYS:
        return True
    start = token.find(v)
    before = token[start - 1] if start > 0 else ""
    after = token[start + len(v)] if start + len(v) < len(token) else ""
    return before not in _NUM_CHARS and after not in _NUM_CHARS


def _sub_box(w: dict, a: str, v: str) -> list[float]:
    """Ước lượng box con khi giá trị nằm bên trong 1 token (vd 'Số:00064098')."""
    start = a.find(v)
    x1, y1, x2, y2 = w["bbox"]
    cw = (x2 - x1) / max(1, len(a))
    return [x1 + cw * start, y1, x1 + cw * (start + len(v)), y2]


def _column_sequence(words: list[dict], by_line: dict[int, list[dict]], s: int, max_lines: int = 3):
    """Duyệt word kiểu ô bảng: hết đoạn trên dòng thì xuống dòng kế, chỉ lấy word cùng cột."""
    first = words[s]
    h = max(4.0, first["bbox"][3] - first["bbox"][1])
    line_id = first["line"]
    row = by_line[line_id]
    k = row.index(first)
    col_x1, col_x2 = first["bbox"][0], first["bbox"][2]
    seg = [first]
    for w in row[k + 1:]:
        if w["bbox"][0] - seg[-1]["bbox"][2] > 1.5 * h:
            break
        seg.append(w)
    for w in seg:
        col_x2 = max(col_x2, w["bbox"][2])
        yield w
    lines_sorted = sorted(by_line)
    pos = lines_sorted.index(line_id)
    for nl in lines_sorted[pos + 1: pos + max_lines]:
        cell = [w for w in by_line[nl]
                if col_x1 - h <= (w["bbox"][0] + w["bbox"][2]) / 2 <= col_x2 + h]
        if not cell:
            return
        gap = min(w["bbox"][1] for w in cell) - max(w["bbox"][3] for w in seg)
        if gap > 1.5 * h:   # khoảng cách dọc quá xa -> không còn cùng ô
            return
        for w in cell:
            yield w
        seg = cell


def find_candidates(page_words: dict[int, list[dict]], key: str, value,
                    max_cands: int = 5) -> list[dict]:
    """Tìm tối đa max_cands ứng viên trên mọi trang. page_words: {page: words đã group_lines}."""
    variants = value_variants(key, value)
    found: list[dict] = []
    for page, words in page_words.items():
        nws = [norm(w["text"]) for w in words]
        n = len(words)
        by_line: dict[int, list[dict]] = {}
        for w in words:
            by_line.setdefault(w["line"], []).append(w)
        for v in variants:
            lv = len(v)
            for s in range(n):
                a = nws[s]
                if not a:
                    continue
                if lv >= 3 and v in a and a != v and _bounded(key, a, v):
                    found.append({"page": page, "bbox": _sub_box(words[s], a, v),
                                  "text": words[s]["text"], "score": 0.97,
                                  "kind": "partial_token", "variant": v,
                                  "source": words[s].get("source")})
                    continue
                if not (v.startswith(a[:3]) or a.startswith(v[:3])):
                    continue
                acc, best, best_n = "", 0.0, 0
                seq = []
                for w in _column_sequence(words, by_line, s):
                    seq.append(w)
                    acc += nws[w["idx"]]
                    if len(acc) > lv + 6 or len(seq) > 60:
                        break
                    sc = 1.0 if acc == v else _ratio(acc, v)
                    if sc > best:
                        best, best_n = sc, len(seq)
                if best >= MIN_SCORE:
                    seg_words = seq[:best_n]
                    lines: dict[int, list] = {}
                    for w in seg_words:
                        lines.setdefault(w["line"], []).append(w["bbox"])
                    found.append({
                        "page": page,
                        "bbox": geometry.union_box([w["bbox"] for w in seg_words]),
                        "segments": [geometry.union_box(b) for b in lines.values()] if len(lines) > 1 else None,
                        "text": " ".join(w["text"] for w in seg_words),
                        "score": round(best, 3), "kind": "words", "variant": v,
                        "source": seg_words[0].get("source"),
                    })
    found.sort(key=lambda c: -c["score"])
    kept: list[dict] = []
    for c in found:
        if any(k["page"] == c["page"] and geometry.iou(k["bbox"], c["bbox"]) > 0.5 for k in kept):
            continue
        kept.append({**c, "bbox": [round(x, 1) for x in c["bbox"]]})
        if len(kept) >= max_cands:
            break
    return kept
