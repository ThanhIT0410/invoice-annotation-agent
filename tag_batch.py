"""Đánh dấu hàng loạt bbox_source=ai cho các hóa đơn đã xử lý trước đó: python tag_batch.py

Gọi PATCH /api/admin/documents/<id> (body {"bbox_source": "ai"}) qua pipeline.agent.tag().

ids_for_tag.txt: mỗi dòng một id, có thể ghi chú sau dấu #. Bỏ qua id đã có marker work/<id>/AI_TAGGED (nên có thể
chạy lại sau khi bị gián đoạn) và id có lần xử lý gần nhất bị abort. Hóa đơn hoàn thành hay chưa hoàn thành đều được tag.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAG_IDS_FILE = ROOT / "ids_for_tag.txt"
sys.path.insert(0, str(ROOT))

from pipeline import config, report  # noqa: E402
from pipeline.agent import tag  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def read_ids() -> list[str]:
    """ids_for_tag.txt: mỗi dòng một id, bỏ dòng trống / # / trùng / id không hợp lệ."""
    if not TAG_IDS_FILE.exists():
        sys.exit(f"Không thấy {TAG_IDS_FILE.name}")
    ids = []
    for line in TAG_IDS_FILE.read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        try:
            if s and s not in ids:
                ids.append(config.check_doc_id(s))
        except ValueError as e:
            print(f"BỎ QUA: {e}")
    return ids


def is_tagged(doc_id: str) -> bool:
    return (config.doc_dir(doc_id) / "AI_TAGGED").exists()


def is_aborted(doc_id: str) -> bool:
    """Lần xử lý gần nhất kết thúc bằng abort: có ABORTED và không có DONE mới hơn (cách run_batch.outcome xét)."""
    d = config.doc_dir(doc_id)
    ab, done = d / "ABORTED", d / "DONE"
    return ab.exists() and (not done.exists() or ab.stat().st_mtime > done.stat().st_mtime)


def tag_one(doc_id: str) -> bool:
    """Gọi API đánh dấu một hóa đơn. Trả True nếu thành công."""
    return tag(doc_id)


def main() -> None:
    if not config.load_session():
        sys.exit("Chưa có x-bbox-session: điền X_BBOX_SESSION trong pipeline/config.py")
    all_ids = read_ids()
    aborted = [i for i in all_ids if is_aborted(i)]
    tagged = [i for i in all_ids if i not in aborted and is_tagged(i)]
    ids = [i for i in all_ids if i not in aborted and i not in tagged]
    print(f"{len(all_ids)} id: {len(tagged)} đã đánh dấu trước đó, {len(aborted)} bị abort (bỏ qua), còn {len(ids)}.")
    for i in aborted:
        print(f"    BỎ QUA {i}: lần xử lý gần nhất bị abort")
    stamp = lambda: datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    report.append_log([f"######## TAG AI BẮT ĐẦU {stamp()} | {len(ids)} hóa đơn ########"]
                      + ([f"   BỎ QUA (abort): {', '.join(aborted)}"] if aborted else []))
    ok, failed = [], []
    for n, doc_id in enumerate(ids, 1):
        if not (config.doc_dir(doc_id) / "DONE").exists():
            print(f"    (lưu ý: {doc_id} không có marker DONE trên máy này)")
        print(f"[{n}/{len(ids)}] {doc_id} ...", flush=True)
        (ok if tag_one(doc_id) else failed).append(doc_id)
    report.append_log([f"######## TAG AI KẾT THÚC {stamp()} | OK: {len(ok)} | LỖI: {len(failed)} ########"]
                      + ([f"   LỖI: {', '.join(failed)}"] if failed else []))
    print(f"Xong. OK: {len(ok)} | LỖI: {len(failed)}")


if __name__ == "__main__":
    main()
