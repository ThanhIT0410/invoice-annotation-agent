"""Xử lý hàng loạt: python run_batch.py

Mỗi id trong ids.txt chạy trong một phiên Claude Code mới; id đã hoàn thành được bỏ qua.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_FILE = ROOT / ".claude" / "skills" / "xu-ly-hoa-don" / "SKILL.md"
sys.path.insert(0, str(ROOT))

from pipeline import config, report  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def find_claude() -> str | None:
    """Chương trình claude: CLAUDE_BIN trong .env -> PATH -> bản đi kèm tiện ích VS Code."""
    if config.CLAUDE_BIN and Path(config.CLAUDE_BIN).exists():
        return config.CLAUDE_BIN
    found = shutil.which("claude")
    if found:
        return found
    ext = Path.home() / ".vscode" / "extensions"
    hits = sorted(ext.glob("anthropic.claude-code-*/**/claude.exe")) + sorted(ext.glob("anthropic.claude-code-*/**/bin/claude"))
    return str(hits[-1]) if hits else None


def read_ids() -> list[str]:
    """ids.txt: mỗi dòng một id, bỏ dòng trống / # / trùng / id không hợp lệ."""
    if not config.IDS_FILE.exists():
        sys.exit(f"Không thấy {config.IDS_FILE.name}")
    ids = []
    for line in config.IDS_FILE.read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        try:
            if s and s not in ids:
                ids.append(config.check_doc_id(s))
        except ValueError as e:
            print(f"BỎ QUA: {e}")
    return ids


def is_done(doc_id: str) -> bool:
    m = config.doc_dir(doc_id) / "DONE"
    return m.exists() and "incomplete" not in m.read_text(encoding="utf-8")


def cache_flags() -> list[str]:
    """Phần giống nhau giữa các hóa đơn đứng trước id để dùng chung cache: SKILL.md vào system prompt,
    bỏ danh sách skill/plugin/MCP/agent (nằm sau id nên hóa đơn nào cũng phải ghi cache lại)."""
    skill = SKILL_FILE.read_text(encoding="utf-8").split("---", 2)[2].strip()
    note = "Quy trình dưới đây chính là skill xu-ly-hoa-don, đã nạp sẵn — không cần gọi tool Skill.\n\n"
    return ["--append-system-prompt", note + skill, "--tools", "Bash,Read,Write,Edit,Grep,Glob",
            "--disable-slash-commands", "--setting-sources", "project", "--strict-mcp-config"]


def run_one(claude: str, doc_id: str) -> dict:
    """Một phiên Claude Code mới cho một hóa đơn; lưu tóm tắt phiên vào claude_session.json."""
    cmd = [claude, "-p", f"Xử lý hóa đơn {doc_id}", "--output-format", "json",
           "--max-turns", str(config.BATCH_MAX_TURNS), "--permission-mode", "acceptEdits", *cache_flags()]
    if config.BATCH_MODEL:
        cmd += ["--model", config.BATCH_MODEL]
    if config.BATCH_EFFORT:
        cmd += ["--effort", config.BATCH_EFFORT]
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")  # dùng Python của venv
    info: dict = {"doc_id": doc_id}
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=config.BATCH_TIMEOUT)
        info["returncode"] = proc.returncode
        try:
            out = json.loads(proc.stdout)
            info.update({k: out.get(k) for k in ("is_error", "num_turns", "total_cost_usd", "session_id", "subtype")})
            info["result"] = (out.get("result") or "")[-2000:]
        except json.JSONDecodeError:
            info["stdout_tail"] = proc.stdout[-2000:]
        info["stderr_tail"] = proc.stderr[-1000:]
    except subprocess.TimeoutExpired:
        info["error"] = f"quá {config.BATCH_TIMEOUT} giây"
    info["seconds"] = round(time.time() - t0, 1)
    d = config.doc_dir(doc_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "claude_session.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def outcome(doc_id: str, started: float) -> str:
    """Kết quả theo marker phiên vừa ghi: COMPLETE / INCOMPLETE / ABORTED / NO_REPORT."""
    d = config.doc_dir(doc_id)
    for name in ("ABORTED", "DONE"):
        m = d / name
        if m.exists() and m.stat().st_mtime >= started - 1:
            if name == "ABORTED":
                return "ABORTED"
            return "INCOMPLETE" if "incomplete" in m.read_text(encoding="utf-8") else "COMPLETE"
    return "NO_REPORT"


def main() -> None:
    claude = find_claude()
    if not claude:
        sys.exit("Không tìm thấy chương trình claude (Claude Code). Đặt CLAUDE_BIN=... trong .env.")
    if not config.load_session():
        sys.exit("Chưa có x-bbox-session: điền X_BBOX_SESSION=... trong .env")
    ids = [i for i in read_ids() if not is_done(i)]
    stamp = lambda: datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    report.append_log([f"######## BATCH BẮT ĐẦU {stamp()} | {len(ids)} hóa đơn ########"])
    tally: dict[str, list[str]] = {}
    for n, doc_id in enumerate(ids, 1):
        print(f"[{n}/{len(ids)}] {doc_id} ...", flush=True)
        started = time.time()
        info = run_one(claude, doc_id)
        res = outcome(doc_id, started)
        if res == "NO_REPORT":  # phiên chết giữa chừng -> vẫn ghi log
            reason = info.get("error") or f"phiên kết thúc (returncode={info.get('returncode')}) nhưng không ghi báo cáo"
            report.write_abort(doc_id, f"[run_batch] {reason}")
        tally.setdefault(res, []).append(doc_id)
        print(f"    -> {res} ({info.get('seconds')}s, {info.get('num_turns')} lượt, ${info.get('total_cost_usd')})")
    summary = " | ".join(f"{k}: {len(v)}" for k, v in tally.items())
    report.append_log([f"######## BATCH KẾT THÚC {stamp()} | {summary} ########"]
                      + [f"   {k}: {', '.join(v)}" for k, v in tally.items() if k != "COMPLETE"])
    print("Xong.", summary)


if __name__ == "__main__":
    main()
