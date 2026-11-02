"""Tổng hợp kết quả một hóa đơn và append vào logs.txt."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import config


def _load(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def _header(doc_id: str, verdict: str) -> list[str]:
    bar = "=" * 24
    return [f"{bar} {_now()} | doc_id={doc_id} {bar}", f"KẾT QUẢ CHUNG: {verdict}"]


def append_log(lines: list[str]) -> None:
    """Append (không ghi đè) một khối báo cáo vào logs.txt."""
    config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with config.LOG_FILE.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n\n")


def _orientation_lines(d: Path, audit: dict) -> list[str]:
    ori = _load(d / "orientation.json", {}) or {}
    notes = {str(o.get("page")): o for o in (audit.get("orientation") or [])}
    if not ori:
        return ["   (chưa xác định orientation)"]
    out = []
    for page, o in sorted(ori.items(), key=lambda kv: int(kv[0])):
        rule = ("xoay ảnh 90° ngược chiều kim đồng hồ để thẳng; bbox quay 180° khi gửi" if o["orientation"] == "landscape"
                else "không xoay ảnh; bbox quay 90° cùng chiều kim đồng hồ khi gửi")
        n = notes.get(str(page), {})
        ok = n.get("upright_ok")
        state = "đã thẳng (Claude xác nhận)" if ok else ("KHÔNG THẲNG" if ok is False else "chưa ghi xác nhận")
        extra = f" — {n['note']}" if n.get("note") else ""
        out.append(f"   - Trang {page}: {o['orientation']} ({rule}); /Rotate PDF={o.get('pdf_rotate')}; {state}{extra}")
    return out


def build_report(doc_id: str) -> tuple[list[str], bool]:
    """Dựng khối log 4 mục bắt buộc. Trả (dòng log, đã hoàn thành toàn bộ bbox hay chưa)."""
    d = config.doc_dir(doc_id)
    meta = _load(d / "meta.json", {}) or {}
    targets = (_load(d / "targets.json", {}) or {}).get("targets", [])
    boxes = {b["field_key"]: b for b in (_load(d / "boxes_upright.json", {}) or {}).get("boxes", [])}
    auto = _load(d / "auto_check.json", {}) or {}
    upload = _load(d / "upload_result.json", {}) or {}
    remote = _load(d / "remote_after.json", None)
    audit = _load(d / "audit.json", None)
    audit_missing = audit is None
    audit = audit or {}

    results = {r["field_key"]: r for r in upload.get("results", [])}
    ok_keys = [k for k, r in results.items() if r.get("ok")]
    failed = [r for r in results.values() if not r.get("ok")]
    n = len(targets)
    missing = [t for t in targets if t["field_key"] not in ok_keys]
    complete = n > 0 and not missing and not failed

    verdict = "HOÀN THÀNH" if complete else "CHƯA HOÀN THÀNH"
    lines = _header(doc_id, verdict)
    if meta.get("pdf_sha256"):
        lines.append(f"PDF sha256={meta['pdf_sha256'][:16]}… | {meta.get('page_count')} trang | "
                     f"nguồn hình học: {meta.get('word_source', '?')}")

    # 1) Hoàn thành bbox
    req = meta.get("required_count")
    ok_box = sum(1 for k in ok_keys if results[k].get("action") == "box")
    ok_nobox = sum(1 for k in ok_keys if results[k].get("action") == "no_box")
    lines.append(f"1) BBOX: đã gửi thành công {len(ok_keys)}/{n} target ({ok_box} box, {ok_nobox} no_box)"
                 + (f" (backend yêu cầu {req})" if req is not None else ""))
    if remote:
        b = (remote.get("document") or remote).get("bbox") or {}
        ts = b.get("total_supplement") or {}
        lines.append(f"   Backend sau khi gửi: status={b.get('status')}, resolved {b.get('resolved_count')}/"
                     f"{b.get('required_count')}, box_count={b.get('box_count')}; "
                     f"tổng tiền: {ts.get('resolved_count')}/{ts.get('required_count')} ({ts.get('status')})")
    for t in targets:
        b = boxes.get(t["field_key"], {})
        if b.get("status") == config.NO_BOX_STATUS:
            reason = b.get("no_box_reason")
            sent = "đã gửi" if t["field_key"] in ok_keys else "CHƯA gửi được"
            lines.append(f"   - no_box {t['field_key']} [{t['label']}]: "
                         f"{config.NO_BOX_REASONS.get(reason, reason)} ({sent})"
                         + (f" — {b['note']}" if b.get("note") and reason != "null_value" else ""))
    for t in missing:
        if boxes.get(t["field_key"], {}).get("status") == config.NO_BOX_STATUS:
            continue  # đã liệt kê ở trên
        st = boxes.get(t["field_key"], {}).get("status", "chưa có box")
        note = boxes.get(t["field_key"], {}).get("note", "")
        lines.append(f"   - THIẾU {t['field_key']} [{t['label']}]: {st}" + (f" — {note}" if note else ""))
    for r in failed:
        lines.append(f"   - GỬI LỖI {r['field_key']}: {r.get('error') or r.get('status_code')}")

    # 2) Sai lệch biểu mẫu
    mism = audit.get("mismatches") or []
    if audit_missing:
        lines.append("2) SAI LỆCH BIỂU MẪU: CHƯA ĐÁNH GIÁ (thiếu audit.json)")
    elif not mism:
        lines.append("2) SAI LỆCH BIỂU MẪU vs HÓA ĐƠN: Không phát hiện sai lệch")
    else:
        lines.append(f"2) SAI LỆCH BIỂU MẪU vs HÓA ĐƠN: CÓ {len(mism)} trường (chỉ báo cáo, KHÔNG sửa)")
        for m in mism:
            lines.append(f"   - {m.get('field_key')}: biểu mẫu={m.get('form_value')!r} | hóa đơn={m.get('invoice_value')!r}"
                         f" (trang {m.get('page', '?')})" + (f" — {m['note']}" if m.get("note") else ""))

    # 3) Orientation
    lines.append("3) ORIENTATION:")
    lines += _orientation_lines(d, audit)

    # 4) Chất lượng từng bbox
    reviews = audit.get("box_reviews") or {}
    flagged = [t for t in targets if (reviews.get(t["field_key"]) or {}).get("verdict") == "issue"]
    if audit_missing:
        lines.append("4) CHẤT LƯỢNG TỪNG BBOX: CHƯA ĐÁNH GIÁ (thiếu audit.json)")
    elif not flagged:
        lines.append("4) CHẤT LƯỢNG TỪNG BBOX: không có box nào có vấn đề")
    else:
        lines.append(f"4) CHẤT LƯỢNG TỪNG BBOX: {len(flagged)} box có vấn đề")
        for t in flagged:
            k, rv = t["field_key"], reviews[t["field_key"]]
            parts = [f"status={boxes.get(k, {}).get('status')}"]
            if rv.get("issues"):
                parts.append("lỗi: " + "; ".join(rv["issues"]))
            if rv.get("note"):
                parts.append(rv["note"])
            auto_issues = [i["msg"] for i in (auto.get(k) or {}).get("issues", [])]
            if auto_issues:
                parts.append("tự kiểm tra: " + "; ".join(auto_issues))
            lines.append(f"   - {k} [{t['label']}]: " + " | ".join(parts))

    if audit.get("notes"):
        lines.append(f"GHI CHÚ: {audit['notes']}")
    return lines, complete


def write_report(doc_id: str) -> bool:
    """Append báo cáo, tạo marker DONE. Trả True nếu hoàn thành toàn bộ bbox."""
    lines, complete = build_report(doc_id)
    append_log(lines)
    d = config.doc_dir(doc_id)
    (d / "DONE").write_text(_now() + ("\ncomplete" if complete else "\nincomplete"), encoding="utf-8")
    return complete


def write_abort(doc_id: str, reason: str) -> None:
    """Dừng quy trình (vd. xoay theo quy tắc mà hóa đơn vẫn không thẳng): ghi log và marker ABORTED."""
    d = config.doc_dir(doc_id)
    lines = _header(doc_id, "DỪNG QUY TRÌNH (ABORT)")
    lines.append(f"LÝ DO: {reason}")
    lines.append("1) BBOX: không gửi bbox nào trong lượt này")
    lines.append("2) SAI LỆCH BIỂU MẪU: chưa đánh giá do dừng sớm")
    lines.append("3) ORIENTATION:")
    lines += _orientation_lines(d, _load(d / "audit.json", {}) or {})
    lines.append("4) CHẤT LƯỢNG TỪNG BBOX: không áp dụng")
    append_log(lines)
    if d.exists():
        (d / "ABORTED").write_text(_now() + "\n" + reason, encoding="utf-8")
