"""도구별 원본 JSONL 레코드 → 공통 Session 모델 변환."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


@dataclass
class Event:
    role: str                       # "user" | "assistant"
    time: Optional[datetime] = None
    text: str = ""
    tool: Optional[str] = None      # 도구 호출이면 도구 이름
    input: Any = None
    diff: Optional[str] = None      # 원본에 변경 내용이 따로 있을 때 (Codex FileChange)
    error: bool = False


@dataclass
class Session:
    tool: str                       # "claude" | "cursor" | "codex"
    id: str = ""
    cwd: Optional[str] = None
    model: Optional[str] = None
    title: Optional[str] = None
    events: list = field(default_factory=list)

    @property
    def start(self) -> Optional[datetime]:
        return next((e.time for e in self.events if e.time), None)

    @property
    def first_prompt(self) -> str:
        return next((e.text for e in self.events if e.role == "user" and e.text), "")


def parse_iso(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _block_text(content) -> str:
    """문자열이거나 [{type, text}, ...] 형태인 content를 하나의 문자열로."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if "text" in b:
                    parts.append(str(b["text"]))
                elif b.get("type") == "image":
                    parts.append("[이미지]")
            else:
                parts.append(str(b))
        return "\n".join(p for p in parts if p)
    return json.dumps(content, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- Claude Code

_COMMAND_RE = re.compile(r"<command-name>(.*?)</command-name>(?:.*?<command-args>(.*?)</command-args>)?", re.S)
_STDOUT_RE = re.compile(r"<local-command-(?:stdout|stderr)>(.*?)</local-command-(?:stdout|stderr)>", re.S)


def _clean_claude_user(text: str) -> str:
    m = _COMMAND_RE.search(text)
    if m:
        return f"`{(m.group(1) + ' ' + (m.group(2) or '')).strip()}`"
    m = _STDOUT_RE.search(text)
    if m:
        out = m.group(1).strip()
        return f"> {out}" if out else ""
    if text.startswith("<local-command-caveat>"):
        return ""
    return text.strip()


def parse_claude(records: list) -> Session:
    s = Session("claude")
    errors = set()
    for r in records:
        content = (r.get("message") or {}).get("content") if r.get("type") == "user" else None
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                    errors.add(b.get("tool_use_id"))

    custom_title = ai_title = None
    for r in records:
        t = r.get("type")
        if t == "custom-title":
            custom_title = r.get("customTitle") or custom_title
        elif t == "ai-title":
            ai_title = r.get("aiTitle") or ai_title
        if t not in ("user", "assistant") or r.get("isSidechain") or r.get("isMeta") or r.get("isCompactSummary"):
            continue
        s.id = s.id or r.get("sessionId", "")
        s.cwd = s.cwd or r.get("cwd")
        time = parse_iso(r.get("timestamp"))
        msg = r.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue

        if t == "user":
            texts = [_clean_claude_user(b.get("text", "")) for b in content if b.get("type") == "text"]
            images = sum(1 for b in content if b.get("type") == "image")
            text = "\n\n".join(x for x in texts if x)
            if images:
                text = (text + f"\n\n[이미지 {images}개]").strip()
            if text:
                s.events.append(Event("user", time, text))
        else:
            if msg.get("model") and msg["model"] != "<synthetic>":
                s.model = msg["model"]
            for b in content:
                if b.get("type") == "text" and b.get("text", "").strip():
                    s.events.append(Event("assistant", time, b["text"].strip()))
                elif b.get("type") == "tool_use":
                    s.events.append(Event("assistant", time, tool=b.get("name"), input=b.get("input"),
                                          error=b.get("id") in errors))
    s.title = custom_title or ai_title
    return s


# ---------------------------------------------------------------- Cursor

_TS_RE = re.compile(r"<timestamp>.*?(\w{3})\w*\.? (\d+), (\d{4}), (\d+):(\d+) ([AP]M) \(UTC([+-]\d+)(?::(\d+))?\).*?</timestamp>", re.S)
_QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.S)
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _cursor_time(text: str) -> Optional[datetime]:
    m = _TS_RE.search(text)
    if not m or m.group(1) not in _MONTHS:
        return None
    mon, day, year, hh, mm, ampm, tzh, tzm = m.groups()
    hour = int(hh) % 12 + (12 if ampm == "PM" else 0)
    offset = int(tzh) * 60 + (int(tzm or 0) if int(tzh) >= 0 else -int(tzm or 0))
    return datetime(int(year), _MONTHS.index(mon) + 1, int(day), hour, int(mm),
                    tzinfo=timezone(timedelta(minutes=offset)))


def parse_cursor(records: list) -> Session:
    s = Session("cursor")
    for r in records:
        content = (r.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        if r.get("role") == "user":
            raw = _block_text([b for b in content if b.get("type") == "text"])
            m = _QUERY_RE.search(raw)
            text = m.group(1).strip() if m else re.sub(r"<timestamp>.*?</timestamp>", "", raw, flags=re.S).strip()
            if text:
                s.events.append(Event("user", _cursor_time(raw), text))
        elif r.get("role") == "assistant":
            for b in content:
                if b.get("type") == "text" and b.get("text", "").strip():
                    s.events.append(Event("assistant", None, b["text"].strip()))
                elif b.get("type") == "tool_use":
                    s.events.append(Event("assistant", None, tool=b.get("name"), input=b.get("input")))
    return s


# ---------------------------------------------------------------- Codex

def _maybe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _codex_file_change(path: str, change: dict) -> str:
    if change.get("unified_diff"):
        return change["unified_diff"].rstrip("\n")
    sign = "-" if change.get("type") == "delete" else "+"
    return "\n".join(sign + ln for ln in str(change.get("content", "")).splitlines())


def parse_codex(records: list) -> Session:
    s = Session("codex")
    for r in records:
        t, p = r.get("type"), r.get("payload") or {}
        time = parse_iso(r.get("timestamp"))
        pt = p.get("type")
        if t == "session_meta":
            s.id = s.id or p.get("id", "")
            s.cwd = s.cwd or p.get("cwd")
        elif t == "turn_context":
            s.model = p.get("model") or s.model
        elif t == "event_msg":
            text = None
            item = p.get("item") or {}
            if pt == "user_message":
                text = p.get("message")
            elif pt == "item_completed" and item.get("type") == "UserMessage":
                text = _block_text(item.get("content"))
            elif pt == "item_completed" and item.get("type") == "FileChange":
                for path, change in (item.get("changes") or {}).items():
                    s.events.append(Event("assistant", time, tool="FileChange", input={"path": path},
                                          diff=_codex_file_change(path, change)))
            if text and text.strip():
                last = s.events[-1] if s.events else None
                if not (last and last.role == "user" and last.text == text.strip()):
                    s.events.append(Event("user", time, text.strip()))
        elif t == "response_item":
            if pt == "message" and p.get("role") == "assistant":
                text = _block_text(p.get("content")).strip()
                if text:
                    s.events.append(Event("assistant", time, text))
            elif pt in ("function_call", "custom_tool_call", "local_shell_call", "web_search_call"):
                inp = _maybe_json(p.get("arguments")) if pt == "function_call" else p.get("input", p.get("action"))
                s.events.append(Event("assistant", time, tool=p.get("name") or pt, input=inp))
    return s


PARSERS = {"claude": parse_claude, "cursor": parse_cursor, "codex": parse_codex}
