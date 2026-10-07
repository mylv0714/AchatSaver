"""원본 위치 탐색, JSONL 증분 읽기, 변환 루프."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from .parsers import PARSERS
from .render import output_path, render


def claude_root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def cursor_root() -> Path:
    return Path.home() / ".cursor" / "projects"


def codex_root() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"


def _norm(name: str) -> str:
    """경로/폴더명을 문자·숫자만 남겨 비교 (각 도구의 경로 인코딩 차이를 흡수)."""
    return "".join(c for c in name if c.isalnum()).lower()


def is_inside(path, project: Path) -> bool:
    if not path:
        return False
    m = re.match(r"^/([a-zA-Z])(/|$)", path)
    if os.name == "nt" and m:  # Git Bash 형식 /d/Fun/x → D:\Fun\x
        path = f"{m.group(1)}:\\" + path[3:]
    p = os.path.normcase(os.path.abspath(path))
    r = os.path.normcase(os.path.abspath(project))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


_codex_cwd = {}


def _codex_session_cwd(path: Path):
    if path not in _codex_cwd:
        cwd = None
        try:
            with open(path, encoding="utf-8") as f:
                first = json.loads(f.readline() or "{}")
            if first.get("type") == "session_meta":
                cwd = first.get("payload", {}).get("cwd")
        except (OSError, ValueError):
            return None  # 아직 쓰는 중일 수 있으니 캐시하지 않고 다음에 다시 시도
        _codex_cwd[path] = cwd
    return _codex_cwd[path]


def find_sources(project: Path):
    """이 프로젝트에 속하는 (도구, 원본 파일) 목록."""
    key = _norm(str(project))
    root = claude_root()
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir() and _norm(d.name).startswith(key):  # cwd 확인은 파싱 후
                for f in d.glob("*.jsonl"):
                    yield "claude", f
    root = cursor_root()
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir() and _norm(d.name) == key:
                yield from (("cursor", f) for f in d.glob("agent-transcripts/*/*.jsonl"))
    root = codex_root()
    if root.is_dir():
        for f in root.glob("*/*/*/rollout-*.jsonl"):
            if is_inside(_codex_session_cwd(f), project):
                yield "codex", f


class JsonlTail:
    """파일에 새로 추가된 완전한 줄만 읽어 records에 누적."""

    def __init__(self, path: Path):
        self.path, self.offset, self.records = path, 0, []

    def update(self) -> bool:
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        if size < self.offset:  # 파일이 새로 쓰였으면 처음부터
            self.offset, self.records = 0, []
        if size == self.offset:
            return False
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read(size - self.offset)
        end = data.rfind(b"\n")
        if end < 0:
            return False
        for line in data[:end].splitlines():
            try:
                self.records.append(json.loads(line))
            except ValueError:
                pass
        self.offset += end + 1
        return True


def _created(path: Path) -> datetime:
    """원본에 시각 정보가 없을 때만 쓰는 대체 시작 시각 (Windows/macOS는 생성 시각)."""
    st = path.stat()
    ts = getattr(st, "st_birthtime", None) or (st.st_ctime if os.name == "nt" else st.st_mtime)
    return datetime.fromtimestamp(ts)


def write_if_changed(path: Path, text: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except (FileNotFoundError, UnicodeDecodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


class Syncer:
    def __init__(self, projects):
        self.projects = [Path(p) for p in projects]
        self.tails = {}
        self.dirty = set()

    def step(self, log=print) -> int:
        """한 바퀴 돌며 바뀐 세션을 다시 씀. 갱신된 파일 수 반환."""
        updated = 0
        for project in self.projects:
            for tool, src in find_sources(project):
                key = (project, src)
                tail = self.tails.setdefault(key, JsonlTail(src))
                if not tail.update() and key not in self.dirty:
                    continue
                session = PARSERS[tool](tail.records)
                session.id = session.id or src.stem
                session.cwd = session.cwd or str(project)  # Cursor 는 원본에 cwd 가 없음
                if tool == "claude" and not is_inside(session.cwd, project):
                    continue
                if not session.first_prompt:
                    continue
                out = output_path(project, session, _created(src))
                try:
                    changed = write_if_changed(out, render(session, str(src)))
                except OSError as e:  # 다른 프로그램이 파일을 잠근 경우 등 → 다음 바퀴에 재시도
                    self.dirty.add(key)
                    log(f"[{datetime.now():%H:%M:%S}] 쓰기 실패, 재시도 예정: {out} ({e})")
                    continue
                self.dirty.discard(key)
                if changed:
                    updated += 1
                    log(f"[{datetime.now():%H:%M:%S}] {out.relative_to(project)}")
        return updated

    def watch(self, interval: float = 1.0, log=print):
        while True:
            self.step(log)
            time.sleep(interval)
