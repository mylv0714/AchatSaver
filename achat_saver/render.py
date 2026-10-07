"""Session → 읽기 좋은 Markdown, 그리고 저장 경로 결정."""
from __future__ import annotations

import difflib
import html
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .parsers import Event, Session

OUTPUT_DIR = "Achat"
TOOL_LABEL = {"claude": "Claude Code", "cursor": "Cursor", "codex": "Codex"}
_SUMMARY_KEYS = ("description", "command", "cmd", "file_path", "path", "pattern", "query",
                 "search_term", "glob_pattern", "url", "skill", "prompt", "target_file")
_PATH_KEYS = ("file_path", "path", "target_file")


def _local(dt: Optional[datetime]) -> Optional[datetime]:
    return dt.astimezone() if dt else None


def fence(text: str, lang: str = "") -> str:
    """본문에 ``` 가 있어도 깨지지 않는 코드 블록."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def _one_line(value, limit: int = 80) -> str:
    s = " ".join(str(value).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _summary(inp) -> str:
    if isinstance(inp, dict):
        for k in _SUMMARY_KEYS:
            if isinstance(inp.get(k), str) and inp[k].strip():
                return _one_line(inp[k])
        return ""
    if isinstance(inp, str):
        return _one_line(inp.strip().splitlines()[0] if inp.strip() else "")
    return ""


def _edit_diff(e: Event) -> str:
    """파일 수정 도구면 unified diff 본문, 아니면 빈 문자열."""
    if e.diff is not None:
        return e.diff
    inp = e.input
    if isinstance(inp, str) and inp.lstrip().startswith("*** Begin Patch"):  # Codex apply_patch
        return inp.strip()
    if not isinstance(inp, dict):
        return ""
    if "old_string" in inp and "new_string" in inp:          # Claude Edit, Cursor StrReplace
        pairs = [(inp["old_string"], inp["new_string"])]
    elif isinstance(inp.get("edits"), list):                  # Claude MultiEdit
        pairs = [(x.get("old_string", ""), x.get("new_string", "")) for x in inp["edits"] if isinstance(x, dict)]
    elif any(k in inp for k in _PATH_KEYS) and isinstance(inp.get("content", inp.get("contents")), str):
        pairs = [("", inp.get("content", inp.get("contents")))]  # Write (새로 쓰기)
    else:
        return ""
    lines = []
    for old, new in pairs:
        lines += list(difflib.unified_diff(str(old).splitlines(), str(new).splitlines(), lineterm=""))[2:]
    return "\n".join(lines)


def _edit_label(e: Event, diff: str, cwd: Optional[str]) -> str:
    path = next((e.input[k] for k in _PATH_KEYS if isinstance(e.input, dict) and e.input.get(k)), "")
    if path and cwd:
        try:
            rel = os.path.relpath(path, cwd)
            path = path if rel.startswith("..") else rel
        except ValueError:  # Windows 에서 드라이브가 다른 경우
            pass
    lines = diff.splitlines()
    plus = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    minus = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    return f"{path or _summary(e.input)} (+{plus} −{minus})"


def _time_label(dt: Optional[datetime], start: Optional[datetime]) -> str:
    dt = _local(dt)
    if not dt:
        return ""
    fmt = "%H:%M" if start and dt.date() == start.date() else "%m-%d %H:%M"
    return " · " + dt.strftime(fmt)


def render(session: Session, source: str = "") -> str:
    start = _local(session.start)
    label = TOOL_LABEL.get(session.tool, session.tool)
    title = session.title or _one_line(session.first_prompt, 60) or "(제목 없음)"
    meta = [f"- 도구: {label}" + (f" · 모델: {session.model}" if session.model else "")]
    if start:
        meta.append(f"- 시작: {start.strftime('%Y-%m-%d %H:%M')}")
    if session.id:
        meta.append(f"- 세션: `{session.id}`")
    if source:
        meta.append(f"- 원본: `{source}`")
    out = [f"# {title}", "", *meta, "", "---"]

    prev_role, prev_was_line = None, False
    for e in session.events:
        if e.role != prev_role:
            who = "🧑 사용자" if e.role == "user" else f"🤖 {label}"
            out += ["", f"## {who}{_time_label(e.time, start)}"]
            prev_role, prev_was_line = e.role, False
        diff = _edit_diff(e) if e.tool else ""
        if e.tool and not diff:  # 일반 도구 호출 → 한 줄 요약 (연속되면 하나의 목록)
            if not prev_was_line:
                out.append("")
            hint = _summary(e.input).replace("`", "'")
            out.append(f"- {'❌' if e.error else '🔧'} **{e.tool}**" + (f" `{hint}`" if hint else ""))
            prev_was_line = True
            continue
        prev_was_line = False
        out.append("")
        if e.tool is None:
            out.append(e.text)
            continue
        out.append(f"<details>\n<summary>✏️ <b>{html.escape(e.tool)}</b> — "
                   f"{html.escape(_edit_label(e, diff, session.cwd))}</summary>\n")
        out.append(fence(diff, "diff"))
        out.append("\n</details>")
    return "\n".join(out).rstrip() + "\n"


def _slug(text: str, limit: int = 30) -> str:
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    s = re.sub(r'[<>:"/\\|?*`#\x00-\x1f]', "", first)
    s = re.sub(r"\s+", "-", s.strip())[:limit].strip(".-")
    return s or "session"


def output_path(project: Path, session: Session, fallback_start: datetime) -> Path:
    """<프로젝트>/Achat/20261008/1556_claude_<첫질문>_<세션ID 끝 6자리>.md"""
    start = _local(session.start) or fallback_start.astimezone()
    name = f"{start:%H%M}_{session.tool}_{_slug(session.first_prompt)}_{session.id[-6:]}.md"
    return project / OUTPUT_DIR / start.strftime("%Y%m%d") / name
