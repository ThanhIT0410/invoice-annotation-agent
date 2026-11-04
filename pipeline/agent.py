"""Lệnh của agent:

    start <id>                          Bước 0–1a: tải PDF + biểu mẫu, render, thumbnail
    rotate <id> <landscape|portrait> [trang...]   Bước 1b: tạo ảnh thẳng (mặc định mọi trang)
    analyze <id>                        Bước 2–6a: manifest, OCR + ứng viên, nháp, refine, review vòng 1
    zoom <id> <trang> <x1,y1,x2,y2>     phóng to vùng ảnh thẳng kèm lưới tọa độ
    ocr-region <id> <trang> <x1,y1,x2,y2>   OCR lại một vùng
    locate <id> <số> <trang> <x1,y1,x2,y2> [<số> <trang> <vùng> ...]   OCR vùng + tìm giá trị + đặt box
    show <id> <số...|all|pending>       xem target: giá trị biểu mẫu, box, ứng viên
    setbox <id> <số> <trang> <x1,y1,x2,y2> [--note]   đặt box tay (tự refine, khóa)
    mark <id> <verified|no_box|needs_review> [lý do no_box] <số...|all> [--note]
    review <id>                         vòng sửa: crop chỉ box vừa sửa
    finish <id>                         Bước 7–9: convert, upload, report (cần audit.json)
    abort <id> <lý do>                  dừng quy trình, ghi logs.txt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import api, config, geometry, match, render, report, verify, words

try:  # in tiếng Việt trên console Windows
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ============================================================ tiện ích
def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _die(msg: str) -> None:
    print(f"LỖI: {msg}")
    sys.exit(2)


def _dir(doc_id: str) -> Path:
    d = config.doc_dir(doc_id)
    if not (d / "meta.json").exists():
        _die(f"chưa có dữ liệu hóa đơn {doc_id} — chạy: start {doc_id}")
    return d


def _short(v, n: int = 60) -> str:
    s = " ".join(str(v).split())  # gộp xuống dòng / khoảng trắng thừa thành một dấu cách
    return s if len(s) <= n else s[: n - 1] + "…"


def _region(s: str) -> list[float]:
    parts = [float(x) for x in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("vùng phải dạng x1,y1,x2,y2")
    return parts


def _now() -> str:
    return datetime.now().astimezone().isoformat()


# ============================================================ Bước 0 — fetch
def fetch(doc_id: str) -> None:
    """Fresh start: xóa work/<id> cũ, tải PDF + biểu mẫu, ghi sha256."""
    d = config.doc_dir(doc_id)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    pdf = d / "original.pdf"
    try:
        doc = api.get_document(doc_id)
        api.download_pdf(doc_id, pdf)
    except api.ApiError as e:
        report.write_abort(doc_id, f"Không lấy được dữ liệu từ API: {e}")
        _die(str(e))
    _save(d / "document.json", doc)
    meta = {"doc_id": doc_id, "fetched_at": _now(),
            "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            "document_sha256": hashlib.sha256((d / "document.json").read_bytes()).hexdigest()}
    _save(d / "meta.json", meta)
    print(f"OK fetch: sha256={meta['pdf_sha256'][:16]}…")


# ============================================================ Bước 1 — render + rotate
def render_pages(doc_id: str) -> None:
    """Render ảnh gốc + thumbnail từng trang."""
    d = _dir(doc_id)
    meta = _load(d / "meta.json")
    pages = render.render_pdf(d / "original.pdf", d / "pages")
    for p in pages:
        thumb = d / "pages" / f"page_{p['page']:03d}_raw_thumb.png"
        render.make_thumbnail(d / p["raw_image"], thumb)
        p["raw_thumb"] = str(thumb.relative_to(d))
    meta.update({"page_count": len(pages), "pages": pages})
    _save(d / "meta.json", meta)
    print(f"OK render: {len(pages)} trang")
    for p in pages:
        print(f"  trang {p['page']}: /Rotate={p['pdf_rotate']}  thumbnail: work/{doc_id}/{p['raw_thumb']}")
    print("TIẾP: Read thumbnail từng trang, xác định orientation theo hướng đọc của chữ, rồi rotate.")


def rotate(doc_id: str, orientation: str, page_nums: list[int]) -> None:
    """Tạo ảnh thẳng + thumbnail theo quy tắc xoay, ghi orientation.json."""
    d = _dir(doc_id)
    meta = _load(d / "meta.json")
    oris = _load(d / "orientation.json", {})
    pages = [p for p in meta["pages"] if not page_nums or p["page"] in page_nums]
    if not pages:
        _die(f"không có trang {page_nums}")
    for p in pages:
        u = d / "pages" / f"page_{p['page']:03d}_upright.png"
        w, h = render.make_upright(d / p["raw_image"], orientation, u)
        thumb = d / "pages" / f"page_{p['page']:03d}_upright_thumb.png"
        render.make_thumbnail(u, thumb)
        oris[str(p["page"])] = {"orientation": orientation, "upright_width": w, "upright_height": h,
                                "pdf_rotate": p["pdf_rotate"], "upright_image": str(u.relative_to(d))}
        print(f"OK trang {p['page']}: {orientation}  thumbnail: work/{doc_id}/{thumb.relative_to(d)}")
    _save(d / "orientation.json", oris)
    print("TIẾP: Read thumbnail ảnh thẳng để XÁC NHẬN chữ đã thẳng; không thẳng -> abort.")


def _orientations(d: Path, meta: dict) -> dict:
    oris = _load(d / "orientation.json", {})
    missing = [p["page"] for p in meta.get("pages", []) if str(p["page"]) not in oris]
    if missing or not meta.get("pages"):
        _die(f"chưa rotate trang {missing or '(chưa render)'}")
    return oris


# ============================================================ Bước 2 — manifest
def build_targets(doc: dict) -> tuple[list[dict], dict]:
    """Biểu mẫu -> danh sách target (mọi trường, kể cả null) + số target backend yêu cầu."""
    document = doc.get("document", doc)
    inv_json = document.get("invoice_json") or {}
    if isinstance(inv_json, str):
        inv_json = json.loads(inv_json)
    targets: list[dict] = []

    def add(fk, group, i, j, key, label, color, value, page):
        is_null = value is None or (isinstance(value, str) and not value.strip())
        targets.append({"field_key": fk, "group": group, "invoice_index": i, "item_index": j,
                        "key": key, "label": label, "color": color,
                        "value": None if is_null else value, "form_null": is_null, "page_hint": page})

    for i, inv in enumerate(inv_json.get("invoices") or []):
        page = inv.get("page_number")
        for path, key, label, color in config.INVOICE_FIELDS:
            add(config.INVOICE_KEY_TEMPLATE.format(i=i, path=path), "invoice", i, None,
                key, label, color, inv.get(key), page)
        for j, item in enumerate(inv.get("items") or []):
            for key, label, color in config.ITEM_FIELDS:
                add(config.ITEM_KEY_TEMPLATE.format(i=i, j=j, key=key), "item", i, j,
                    key, label, color, item.get(key), page)
        for path, key, label, color in config.TOTAL_FIELDS:
            val = inv.get(key)
            if val is None:
                val = (inv.get("totals") or {}).get(key)
            add(config.INVOICE_KEY_TEMPLATE.format(i=i, path=path), "total", i, None,
                key, label, color, val, page)

    bbox = document.get("bbox") or {}
    ts = bbox.get("total_supplement") or {}
    req = None
    if bbox.get("required_count") is not None:
        req = int(bbox.get("required_count") or 0) + int(ts.get("required_count") or 0)
    return targets, {"required_count": req}


def manifest(doc_id: str) -> None:
    """Lập targets.json từ biểu mẫu."""
    d = _dir(doc_id)
    meta = _load(d / "meta.json")
    targets, info = build_targets(_load(d / "document.json"))
    _save(d / "targets.json", {"doc_id": doc_id, "targets": targets})
    meta.update(info)
    _save(d / "meta.json", meta)
    n_null = sum(1 for t in targets if t["form_null"])
    n_items = len({(t["invoice_index"], t["item_index"]) for t in targets if t["group"] == "item"})
    print(f"OK manifest: {len(targets)} target ({n_items} dòng hàng), {n_null} trường null -> no_box, "
          f"backend yêu cầu {info['required_count']}")
    if not targets:
        print("  CẢNH BÁO: biểu mẫu không có target nào.")


# ============================================================ Bước 3 — OCR + ứng viên + nháp
def candidates(doc_id: str) -> None:
    """OCR từng trang trên ảnh thẳng, sinh ứng viên cho từng target."""
    d = _dir(doc_id)
    meta = _load(d / "meta.json")
    oris = _orientations(d, meta)
    targets = _load(d / "targets.json")["targets"]
    page_words: dict[int, list[dict]] = {}
    for p in meta["pages"]:
        with Image.open(d / oris[str(p["page"])]["upright_image"]) as im:
            ws = words.group_lines(words.ocr_words(im, psm=config.OCR_PSM_PAGE))
        _save(d / "words" / f"words_p{p['page']:03d}.json", ws)
        page_words[p["page"]] = ws
    meta["word_source"] = f"ocr:tesseract:{config.OCR_LANG}"
    _save(d / "meta.json", meta)
    cands = {t["field_key"]: ([] if t["form_null"] else match.find_candidates(page_words, t["key"], t["value"]))
             for t in targets}
    _save(d / "candidates.json", cands)
    print(f"OK OCR: {sum(len(w) for w in page_words.values())} word")


def _cy(box) -> float:
    return (box[1] + box[3]) / 2


def _row_anchors(targets, cands) -> dict:
    """Tâm dọc (trang, y) của từng dòng hàng, lấy từ trường chỉ có 1 ứng viên tốt."""
    ys: dict[tuple, list] = {}
    for t in targets:
        if t["group"] == "item":
            good = [c for c in cands.get(t["field_key"]) or [] if c["score"] >= config.DRAFT_MIN_SCORE]
            if len(good) == 1:
                ys.setdefault((t["invoice_index"], t["item_index"]), []).append((good[0]["page"], _cy(good[0]["bbox"])))
    out = {}
    for k, v in ys.items():
        page = max({p for p, _ in v}, key=[p for p, _ in v].count)
        yy = sorted(y for p, y in v if p == page)
        out[k] = (page, yy[len(yy) // 2])
    return out


def _prefer_row(t, cs, anchors) -> list:
    """Trường hàng có nhiều ứng viên tốt: ưu tiên ứng viên gần dòng của item."""
    anchor = anchors.get((t["invoice_index"], t["item_index"])) if t["group"] == "item" else None
    good = [c for c in cs if c["score"] >= config.DRAFT_MIN_SCORE]
    if not anchor or len(good) < 2:
        return cs
    page, y = anchor
    good.sort(key=lambda c: (c["page"] != page, abs(_cy(c["bbox"]) - y), -c["score"]))
    return good + [c for c in cs if c["score"] < config.DRAFT_MIN_SCORE]


def draft(doc_id: str) -> None:
    """boxes_upright.json nháp: ứng viên tốt nhất -> needs_review; null -> no_box."""
    d = _dir(doc_id)
    targets = _load(d / "targets.json")["targets"]
    cands = _load(d / "candidates.json")
    anchors = _row_anchors(targets, cands)
    ranked = {t["field_key"]: _prefer_row(t, cands.get(t["field_key"]) or [], anchors) for t in targets}
    first = {k: cs[0] for k, cs in ranked.items() if cs and cs[0]["score"] >= config.DRAFT_MIN_SCORE}

    def inside_other(k, c) -> bool:
        """Ứng viên nằm lọt trong box nháp lớn hơn của trường khác (vd "VIÊN" cuối tên thuốc).
        Box trùng nhau do cùng giá trị (vd tổng tiền = thành tiền) để Claude chọn theo nhãn."""
        return any(kk != k and o["page"] == c["page"]
                   and geometry.area(o["bbox"]) > geometry.area(c["bbox"])
                   and geometry.intersection(o["bbox"], c["bbox"]) > 0.5 * geometry.area(c["bbox"])
                   for kk, o in first.items())

    boxes = []
    for t in targets:
        cs = ranked[t["field_key"]]
        good = [c for c in cs if c["score"] >= config.DRAFT_MIN_SCORE]
        if len(good) > 1 and inside_other(t["field_key"], good[0]):
            alt = [c for c in good if not inside_other(t["field_key"], c)]
            if alt:
                cs = alt + [c for c in cs if c not in alt]
        base = {"field_key": t["field_key"], "page": t.get("page_hint") or 1, "bbox": None, "method": None}
        if t["form_null"]:
            boxes.append({**base, "status": "no_box", "no_box_reason": "null_value", "note": "biểu mẫu để null"})
        elif cs and cs[0]["score"] >= config.DRAFT_MIN_SCORE:
            c = cs[0]
            boxes.append({**base, "page": c["page"], "bbox": c["bbox"], "status": "needs_review", "method": "ocr",
                          "note": f"nháp: ứng viên score={c['score']} ({len(cs)} ứng viên)"})
        else:
            boxes.append({**base, "status": "needs_review", "note": "chưa có ứng viên — tìm bằng zoom/ocr-region"})
    _save(d / "boxes_upright.json", {"doc_id": doc_id, "boxes": boxes})
    n_box = sum(1 for b in boxes if b["bbox"])
    n_null = sum(1 for b in boxes if b["status"] == "no_box")
    print(f"OK nháp: {n_box} có box, {n_null} no_box (null), {len(boxes) - n_box - n_null} chưa có ứng viên")


def _load_boxes(d: Path) -> tuple[list[dict], dict, dict]:
    """Đọc và kiểm tra boxes_upright.json; dừng nếu sai."""
    oris = _orientations(d, _load(d / "meta.json"))
    targets = {t["field_key"]: t for t in _load(d / "targets.json")["targets"]}
    data = _load(d / "boxes_upright.json") or _die("chưa có boxes_upright.json — chạy analyze")
    errs, seen = [], set()
    for b in data.get("boxes", []):
        k, st = b.get("field_key"), b.get("status")
        if k not in targets:
            errs.append(f"{k}: không có trong targets.json")
            continue
        if k in seen:
            errs.append(f"{k}: bị lặp")
        seen.add(k)
        if st not in config.VALID_STATUSES:
            errs.append(f"{k}: status không hợp lệ {st!r}")
        if st == config.NO_BOX_STATUS:
            if b.get("bbox") is not None:
                errs.append(f"{k}: no_box nhưng vẫn có bbox")
            if b.get("no_box_reason") not in config.NO_BOX_REASONS:
                errs.append(f"{k}: no_box thiếu/sai lý do")
            continue
        if targets[k].get("form_null"):
            errs.append(f"{k}: biểu mẫu null nên phải là no_box")
        if b.get("bbox") is not None:
            o = oris.get(str(b.get("page")))
            x1, y1, x2, y2 = b["bbox"]
            if not o or not (0 <= x1 < x2 <= o["upright_width"] and 0 <= y1 < y2 <= o["upright_height"]):
                errs.append(f"{k}: bbox {b['bbox']} sai trang hoặc ngoài ảnh")
        elif st == "verified":
            errs.append(f"{k}: verified nhưng bbox=null")
    if errs:
        _die("boxes_upright.json không hợp lệ:\n  - " + "\n  - ".join(errs))
    return data["boxes"], targets, oris


def _save_boxes(d: Path, boxes: list[dict]) -> None:
    _save(d / "boxes_upright.json", {"doc_id": d.name, "boxes": boxes})


# ============================================================ Bước 5 — refine
def refine(doc_id: str) -> None:
    """Bó box sát nét chữ, padding thích ứng (trừ box locked); lưu before_bbox."""
    d = _dir(doc_id)
    boxes, _, oris = _load_boxes(d)
    grays, log = {}, _load(d / "refine_log.json", [])
    for b in boxes:
        if not b.get("bbox") or b.get("locked"):
            continue
        p = b["page"]
        if p not in grays:
            grays[p] = verify.load_gray(d / oris[str(p)]["upright_image"])
        new, info = verify.tighten(grays[p], b["bbox"])
        if info.get("changed"):
            log.append({"field_key": b["field_key"], "before": b["bbox"], "after": new, "info": info, "at": _now()})
            b.setdefault("before_bbox", b["bbox"])
            b["bbox"] = new
    _save_boxes(d, boxes)
    _save(d / "refine_log.json", log)


# ============================================================ Bước 6 — review
def _review_state(d: Path) -> dict:
    return _load(d / "review_state.json", {"round": 0, "reviewed": {}})


def review(doc_id: str) -> None:
    """Tự kiểm tra mọi box; crop chỉ box mới/vừa sửa; tối đa MAX_REVIEW_ROUNDS vòng."""
    d = _dir(doc_id)
    boxes, targets, oris = _load_boxes(d)
    state = _review_state(d)
    index = {b["field_key"]: n for n, b in enumerate(boxes)}
    with_box = [b for b in boxes if b.get("bbox")]
    changed = [b for b in with_box if state["reviewed"].get(b["field_key"]) != b["bbox"]]
    if not changed:
        print("OK review: không có box mới hoặc vừa sửa.")
        return
    rnd = state["round"] + 1
    if rnd > config.MAX_REVIEW_ROUNDS:
        _die(f"hết {config.MAX_REVIEW_ROUNDS} vòng review (1 vòng đầu + {config.MAX_REVIEW_ROUNDS - 1} vòng sửa). "
             "Box chưa được xem: " + ", ".join(f"#{index[b['field_key']]}" for b in changed)
             + " — mark needs_review kèm lý do rồi đi tiếp.")

    imgs, grays, auto = {}, {}, {}
    overlaps = verify.overlap_issues(with_box)
    for b in with_box:  # tự kiểm tra mọi box (chỉ ra chữ)
        p = b["page"]
        if p not in grays:
            grays[p] = verify.load_gray(d / oris[str(p)]["upright_image"])
        res = verify.analyze_box(grays[p], b["bbox"])
        res["issues"] += overlaps.get(b["field_key"], [])
        res["ok"] = not res["issues"]
        auto[b["field_key"]] = res
    _save(d / "auto_check.json", auto)

    def img(p):
        if p not in imgs:
            imgs[p] = Image.open(d / oris[str(p)]["upright_image"]).convert("RGB")
        return imgs[p]

    def moved(b) -> bool:  # box mới, hoặc tâm dịch xa hơn chiều cao box (có thể đổi hàng)
        old = state["reviewed"].get(b["field_key"])
        if old is None:
            return True
        return max(abs(_cy(old) - _cy(b["bbox"])), abs((old[0] + old[2] - b["bbox"][0] - b["bbox"][2]) / 2)) \
            > b["bbox"][3] - b["bbox"][1]

    rdir = d / "review" / f"round_{rnd}"
    shutil.rmtree(rdir, ignore_errors=True)
    rdir.mkdir(parents=True)
    tiles = []
    for b in changed:
        t, res = targets[b["field_key"]], auto[b["field_key"]]
        codes = ", ".join(x["code"] for x in res["issues"]) or "OK"
        tiles.append(verify.render_crop(img(b["page"]), b["bbox"], t["color"], [
            f"#{index[b['field_key']]} [{t['label']}] trang {b['page']}",
            f"biểu mẫu: {_short(t['value'], 60)} | tự kiểm tra: {codes}",
        ], text_h=res.get("metrics", {}).get("line_h")))
    shots = verify.pack_sheets(tiles, rdir, "sheet")
    for p in sorted({b["page"] for b in changed if moved(b)}):  # ảnh phủ box: kiểm tra nhầm hàng
        items = [{"bbox": b["bbox"], "color": targets[b["field_key"]]["color"], "tag": index[b["field_key"]]}
                 for b in with_box if b["page"] == p]
        path = rdir / f"overlay_page_{p:03d}.png"
        verify.draw_overlay(img(p), items, config.OVERLAY_LONG_EDGE).save(path)
        shots.append(path)
    for b in changed:
        state["reviewed"][b["field_key"]] = b["bbox"]
    state["round"] = rnd
    _save(d / "review_state.json", state)

    print(f"OK review vòng {rnd}/{config.MAX_REVIEW_ROUNDS}: {len(changed)} crop, {len(shots)} ảnh")
    for b in changed:
        issues = auto[b["field_key"]]["issues"]
        print(f"  #{index[b['field_key']]} {b['field_key']}" + ("  ⚠ " + "; ".join(x["msg"] for x in issues) if issues else ""))
    pending = [f"#{index[b['field_key']]}" for b in boxes if not b.get("bbox") and b["status"] != config.NO_BOX_STATUS]
    if pending:
        print(f"  CHƯA CÓ BOX: {', '.join(pending)}")
    print("Ảnh cần Read (xem TỪNG crop):")
    for x in shots:
        print(f"  work/{doc_id}/{x.relative_to(d)}")


# ============================================================ quyết định của Claude
def _resolve_keys(tokens: list[str], boxes: list[dict]) -> list[str]:
    """Số thứ tự (#3 / 3) hoặc field_key -> field_key."""
    keys = []
    for tok in tokens:
        t = tok.lstrip("#")
        if t.isdigit() and 0 <= int(t) < len(boxes):
            keys.append(boxes[int(t)]["field_key"])
        elif any(b["field_key"] == tok for b in boxes):
            keys.append(tok)
        else:
            _die(f"không có target {tok!r}")
    return keys


def mark(doc_id: str, status: str, tokens: list[str], note: str | None) -> None:
    """Đặt trạng thái nhiều target một lần. no_box: token đầu là lý do. 'all' = mọi box đang chờ."""
    d = _dir(doc_id)
    boxes, _, _ = _load_boxes(d)
    state = _review_state(d)
    reason = None
    if status == config.NO_BOX_STATUS:
        if not tokens or tokens[0] not in config.NO_BOX_REASONS:
            _die(f"no_box cần lý do đứng trước số thứ tự: {', '.join(config.NO_BOX_REASONS)}")
        reason, tokens = tokens[0], tokens[1:]
    if tokens == ["all"]:
        keys = [b["field_key"] for b in boxes if b["status"] == "needs_review" and b.get("bbox")]
    else:
        keys = _resolve_keys(tokens, boxes)
    if not keys:
        _die("chưa chỉ định target nào")
    by = {b["field_key"]: b for b in boxes}
    if status == "verified":
        bad = [f"#{boxes.index(by[k])}" for k in keys
               if not by[k].get("bbox") or state["reviewed"].get(k) != by[k]["bbox"]]
        if bad:
            _die(f"chưa xem crop của box hiện tại: {', '.join(bad)} — chạy review trước")
    for k in keys:
        b = by[k]
        b["status"] = status
        if reason:
            b["bbox"], b["no_box_reason"] = None, reason
        else:
            b.pop("no_box_reason", None)
        if note:
            b["note"] = note
    _save_boxes(d, boxes)
    print(f"OK {status}: " + ", ".join(f"#{boxes.index(by[k])}" for k in keys))


def setbox(doc_id: str, token: str, page: int, region: list[float], note: str | None) -> None:
    """Box tay từ zoom/ocr-region: refine theo nét chữ rồi khóa."""
    d = _dir(doc_id)
    boxes, _, oris = _load_boxes(d)
    k = _resolve_keys([token], boxes)[0]
    b = next(x for x in boxes if x["field_key"] == k)
    o = oris.get(str(page)) or _die(f"trang {page} chưa rotate")
    x1, y1, x2, y2 = region
    if not (0 <= x1 < x2 <= o["upright_width"] and 0 <= y1 < y2 <= o["upright_height"]):
        _die(f"vùng ngoài ảnh thẳng {o['upright_width']}x{o['upright_height']}")
    box, _ = verify.tighten(verify.load_gray(d / o["upright_image"]), region)
    b.update({"page": page, "bbox": box, "status": "needs_review", "method": "manual",
              "locked": True, "before_bbox": region})
    b.pop("no_box_reason", None)
    if note:
        b["note"] = note
    _save_boxes(d, boxes)
    print(f"OK setbox #{boxes.index(b)}: {[int(v) for v in box]} -> chạy review")


def zoom(doc_id: str, page: int, region: list[float]) -> None:
    """Phóng to vùng ảnh thẳng kèm lưới tọa độ."""
    d = _dir(doc_id)
    o = _load(d / "orientation.json", {}).get(str(page)) or _die(f"trang {page} chưa rotate")
    with Image.open(d / o["upright_image"]) as im:
        z = verify.ruler_zoom(im, region, config.ZOOM_SCALE, config.ZOOM_GRID)
    out = d / "zoom" / f"zoom_p{page:03d}_{'_'.join(str(int(v)) for v in region)}.png"
    out.parent.mkdir(exist_ok=True)
    z.save(out)
    print(f"OK work/{doc_id}/{out.relative_to(d)} (lưới = pixel ảnh thẳng)")


def _ocr_crop(d: Path, o: dict, region: list[float]) -> list[dict]:
    """OCR một vùng ảnh thẳng (phóng to), trả word đã group_lines với tọa độ ảnh thẳng."""
    x1, y1, x2, y2 = [int(v) for v in region]
    s = config.OCR_REGION_SCALE
    with Image.open(d / o["upright_image"]) as im:
        crop = im.crop((x1, y1, x2, y2))
        crop = crop.resize((int(crop.width * s), int(crop.height * s)), Image.LANCZOS)
        return words.group_lines(words.ocr_words(crop, offset=(x1, y1), scale=s, psm=config.OCR_PSM_REGION))


def ocr_region(doc_id: str, page: int, region: list[float]) -> None:
    """OCR lại một vùng phóng to, in word kèm tọa độ ảnh thẳng."""
    d = _dir(doc_id)
    o = _load(d / "orientation.json", {}).get(str(page)) or _die(f"trang {page} chưa rotate")
    ws = _ocr_crop(d, o, region)
    for w in ws:
        print(f"  {[int(v) for v in w['bbox']]} conf={w['conf']:.0f} {w['text']}")
    if not ws:
        print("  (không đọc được chữ nào)")


def locate(doc_id: str, items: list[str]) -> None:
    """Mỗi bộ (số, trang, vùng): OCR vùng, tìm giá trị biểu mẫu, refine rồi khóa box.
    Không tìm thấy -> in chữ OCR đọc được, giữ nguyên box."""
    if len(items) % 3:
        _die("locate cần từng bộ ba: <số> <trang> <x1,y1,x2,y2>")
    d = _dir(doc_id)
    boxes, targets, oris = _load_boxes(d)
    jobs = []  # kiểm tra mọi bộ trước khi làm, để lỗi cú pháp không làm mất kết quả bộ trước
    for i in range(0, len(items), 3):
        try:
            jobs.append((_resolve_keys([items[i]], boxes)[0], int(items[i + 1]), _region(items[i + 2])))
        except (ValueError, argparse.ArgumentTypeError):
            _die(f"bộ '{items[i]} {items[i + 1]} {items[i + 2]}': trang hoặc vùng sai")
    gray: dict[int, object] = {}
    changed = False
    for k, page, region in jobs:
        b = next(x for x in boxes if x["field_key"] == k)
        t, n = targets[k], f"#{boxes.index(b)}"
        o = oris.get(str(page))
        if not o:
            print(f"  {n}: BỎ QUA — trang {page} chưa rotate")
            continue
        x1, y1, x2, y2 = region
        if not (0 <= x1 < x2 <= o["upright_width"] and 0 <= y1 < y2 <= o["upright_height"]):
            print(f"  {n}: BỎ QUA — vùng ngoài ảnh thẳng {o['upright_width']}x{o['upright_height']}")
            continue
        if t["form_null"]:
            print(f"  {n}: BỎ QUA — biểu mẫu null, giữ no_box")
            continue
        ws = _ocr_crop(d, o, region)
        cs = match.find_candidates({page: ws}, t["key"], t["value"])
        if not cs:
            lines: dict[int, list] = {}
            for w in ws:
                lines.setdefault(w["line"], []).append(w["text"])
            read = " | ".join(" ".join(x) for x in lines.values()) or "(không đọc được chữ nào)"
            print(f"  {n}: KHÔNG THẤY {_short(t['value'], 40)!r} — OCR đọc: {_short(read, 200)}")
            continue
        c = cs[0]
        if page not in gray:
            gray[page] = verify.load_gray(d / o["upright_image"])
        box, _ = verify.tighten(gray[page], c["bbox"])
        b.update({"page": page, "bbox": box, "status": "needs_review", "method": "locate",
                  "locked": True, "before_bbox": c["bbox"],
                  "note": f"locate: '{_short(c['text'], 40)}' score={c['score']}"})
        b.pop("no_box_reason", None)
        changed = True
        more = f" (vùng có {len(cs)} ứng viên — thu hẹp vùng nếu chọn sai)" if len(cs) > 1 else ""
        print(f"  {n}: OK {[int(v) for v in box]} '{_short(c['text'], 40)}' score={c['score']}{more}")
    if changed:
        _save_boxes(d, boxes)
        print("-> chạy review")


def show(doc_id: str, tokens: list[str]) -> None:
    """In thông tin target: giá trị biểu mẫu, trạng thái, box, 3 ứng viên đầu."""
    d = _dir(doc_id)
    boxes, targets, _ = _load_boxes(d)
    cands = _load(d / "candidates.json", {})
    if tokens == ["all"]:
        keys = [b["field_key"] for b in boxes]
    elif tokens == ["pending"]:
        keys = [b["field_key"] for b in boxes if b["status"] == "needs_review"]
    else:
        keys = _resolve_keys(tokens, boxes)
    by = {b["field_key"]: b for b in boxes}
    for k in keys:
        b, t = by[k], targets[k]
        box = [int(v) for v in b["bbox"]] if b.get("bbox") else None
        extra = f" lý do={b['no_box_reason']}" if b.get("no_box_reason") else ""
        print(f"#{boxes.index(b)} {k} — {t['label']}")
        print(f"   biểu mẫu: {_short(t['value'], 80)!r} | {b['status']}{extra} | trang {b['page']} box {box}")
        for c in (cands.get(k) or [])[:3]:
            print(f"   ứng viên: trang {c['page']} {[int(v) for v in c['bbox']]} score={c['score']} '{_short(c['text'], 50)}'")
    if not keys:
        print("(không có target nào)")


# ============================================================ Bước 7–9
def convert(doc_id: str) -> None:
    """verified -> request box (tọa độ backend); no_box -> request no_box."""
    d = _dir(doc_id)
    boxes, targets, oris = _load_boxes(d)
    state = _review_state(d)
    missing = sorted(set(targets) - {b["field_key"] for b in boxes})
    if missing:
        _die(f"thiếu target: {', '.join(missing)}")
    unseen = [b["field_key"] for b in boxes if b["status"] == "verified" and state["reviewed"].get(b["field_key"]) != b["bbox"]]
    if unseen:
        _die("box verified chưa được xem crop sau lần sửa cuối: " + ", ".join(unseen))
    payloads = []
    for b in boxes:
        if b["status"] == config.NO_BOX_STATUS:
            payloads.append(config.no_box_payload(b["field_key"]))
        elif b["status"] == "verified":
            o, t = oris[str(b["page"])], targets[b["field_key"]]
            pts = geometry.upright_box_to_backend_points(b["bbox"], o["orientation"], o["upright_width"],
                                                         o["upright_height"], config.COORD_DIGITS)
            payloads.append({"field_key": b["field_key"], "action": "box", "box": {
                "fieldKey": b["field_key"], "label": t["label"], "color": t["color"],
                "pageNumber": int(b["page"]), "points": pts}})
    _save(d / "payloads.json", payloads)
    n_nobox = sum(1 for pl in payloads if pl["action"] == "no_box")
    print(f"OK convert: {len(payloads) - n_nobox} box, {n_nobox} no_box, "
          f"{sum(1 for b in boxes if b['status'] == 'needs_review')} needs_review (không gửi)")


def upload(doc_id: str) -> None:
    """PUT từng target, rồi GET lại trạng thái backend."""
    d = _dir(doc_id)
    results = []
    for pl in _load(d / "payloads.json"):
        try:
            code, body = api.put_box(doc_id, pl)
            results.append({"field_key": pl["field_key"], "action": pl["action"], "ok": 200 <= code < 300,
                            "status_code": code, "body": body})
        except api.ApiError as e:
            results.append({"field_key": pl["field_key"], "action": pl["action"], "ok": False, "error": str(e)})
    _save(d / "upload_result.json", {"at": _now(), "results": results})
    print(f"OK upload: {sum(1 for r in results if r['ok'])}/{len(results)} thành công")
    for r in results:
        if not r["ok"]:
            print(f"  LỖI {r['field_key']}: {r.get('error') or r.get('status_code')}")
    try:
        _save(d / "remote_after.json", api.get_document(doc_id))
    except api.ApiError as e:
        print(f"  Không đọc lại được trạng thái backend: {e}")


def tag(doc_id: str) -> bool:
    """Đánh dấu bbox_source=ai; lưu phản hồi vào ai_tag.json, tạo marker AI_TAGGED nếu thành công."""
    d = config.doc_dir(doc_id)
    d.mkdir(parents=True, exist_ok=True)
    try:
        code, body = api.tag_ai_source(doc_id)
        res = {"at": _now(), "ok": 200 <= code < 300, "status_code": code, "body": body}
    except api.ApiError as e:
        res = {"at": _now(), "ok": False, "error": str(e)}
    _save(d / "ai_tag.json", res)
    if res["ok"]:
        (d / "AI_TAGGED").write_text(res["at"], encoding="utf-8")
    status = "OK" if res["ok"] else f"LỖI {res.get('error') or res.get('status_code')}"
    report.append_log([f"[{doc_id}] bbox_source=ai: {status}"])
    print(f"{'OK' if res['ok'] else 'LỖI'} tag: {status}")
    return res["ok"]


# ============================================================ lệnh
def cmd_start(a) -> None:
    fetch(a.doc_id)
    render_pages(a.doc_id)


def cmd_analyze(a) -> None:
    manifest(a.doc_id)
    candidates(a.doc_id)
    draft(a.doc_id)
    refine(a.doc_id)
    (config.doc_dir(a.doc_id) / "review_state.json").unlink(missing_ok=True)
    review(a.doc_id)


def cmd_finish(a) -> None:
    if not (_dir(a.doc_id) / "audit.json").exists():
        _die("chưa có audit.json — viết audit trước khi finish")
    convert(a.doc_id)
    upload(a.doc_id)
    complete = report.write_report(a.doc_id)
    print(f"OK report: {'HOÀN THÀNH' if complete else 'CHƯA HOÀN THÀNH'}")
    tag(a.doc_id)


def cmd_abort(a) -> None:
    report.write_abort(a.doc_id, a.reason)
    print(f"ĐÃ DỪNG {a.doc_id}: {a.reason}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m pipeline.agent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    doc_id = lambda p: p.add_argument("doc_id", type=config.check_doc_id)

    p = sp.add_parser("start"); doc_id(p); p.set_defaults(fn=cmd_start)
    p = sp.add_parser("rotate"); doc_id(p)
    p.add_argument("orientation", choices=geometry.ORIENTATIONS); p.add_argument("pages", nargs="*", type=int)
    p.set_defaults(fn=lambda a: rotate(a.doc_id, a.orientation, a.pages))
    p = sp.add_parser("analyze"); doc_id(p); p.set_defaults(fn=cmd_analyze)
    for name, fn in (("zoom", zoom), ("ocr-region", ocr_region)):
        p = sp.add_parser(name); doc_id(p); p.add_argument("page", type=int); p.add_argument("region", type=_region)
        p.set_defaults(fn=lambda a, fn=fn: fn(a.doc_id, a.page, a.region))
    p = sp.add_parser("locate"); doc_id(p); p.add_argument("items", nargs="+")
    p.set_defaults(fn=lambda a: locate(a.doc_id, a.items))
    p = sp.add_parser("show"); doc_id(p); p.add_argument("tokens", nargs="+")
    p.set_defaults(fn=lambda a: show(a.doc_id, a.tokens))
    p = sp.add_parser("setbox"); doc_id(p)
    p.add_argument("key"); p.add_argument("page", type=int); p.add_argument("region", type=_region); p.add_argument("--note")
    p.set_defaults(fn=lambda a: setbox(a.doc_id, a.key, a.page, a.region, a.note))
    p = sp.add_parser("mark"); doc_id(p)
    p.add_argument("status", choices=sorted(config.VALID_STATUSES)); p.add_argument("tokens", nargs="+"); p.add_argument("--note")
    p.set_defaults(fn=lambda a: mark(a.doc_id, a.status, a.tokens, a.note))
    p = sp.add_parser("review"); doc_id(p); p.set_defaults(fn=lambda a: review(a.doc_id))
    p = sp.add_parser("finish"); doc_id(p); p.set_defaults(fn=cmd_finish)
    p = sp.add_parser("abort"); doc_id(p); p.add_argument("reason"); p.set_defaults(fn=cmd_abort)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()