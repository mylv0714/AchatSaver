"""achat 명령행: init / remove / list / sync / watch"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .render import OUTPUT_DIR
from .watcher import Syncer

CONFIG = Path.home() / ".achat" / "projects.json"


def load_projects() -> list:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


def save_projects(projects: list):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_gitignore(project: Path):
    gi = project / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if any(ln.strip().strip("/") == OUTPUT_DIR for ln in text.splitlines()):
        return
    sep = "" if not text or text.endswith("\n") else "\n"
    gi.write_text(f"{text}{sep}{OUTPUT_DIR}/\n", encoding="utf-8")


def cmd_init(args):
    project = Path(args.path).resolve()
    (project / OUTPUT_DIR).mkdir(exist_ok=True)
    ensure_gitignore(project)
    projects = load_projects()
    if str(project) not in projects:
        projects.append(str(project))
        save_projects(projects)
    print(f"등록됨: {project}\n대화 저장 위치: {project / OUTPUT_DIR}")
    n = Syncer([project]).step(log=lambda *_: None)
    print(f"기존 대화 {n}개 변환 완료. 실시간 저장은 `achat watch` 를 실행하세요.")


def cmd_remove(args):
    project = str(Path(args.path).resolve())
    projects = load_projects()
    if project in projects:
        projects.remove(project)
        save_projects(projects)
        print(f"등록 해제됨: {project} (이미 저장된 {OUTPUT_DIR}/ 폴더는 그대로 둡니다)")
    else:
        print(f"등록되지 않은 프로젝트입니다: {project}")


def cmd_list(args):
    projects = load_projects()
    print("\n".join(projects) if projects else "등록된 프로젝트가 없습니다. `achat init` 으로 등록하세요.")


def _targets(args) -> list:
    if getattr(args, "path", None):
        return [str(Path(args.path).resolve())]
    projects = load_projects()
    if not projects:
        sys.exit("등록된 프로젝트가 없습니다. 프로젝트 폴더에서 `achat init` 을 먼저 실행하세요.")
    return projects


def cmd_sync(args):
    n = Syncer(_targets(args)).step()
    print(f"{n}개 파일 갱신")


def cmd_watch(args):
    projects = _targets(args)
    print("감시 중 (Ctrl+C 로 종료):\n  " + "\n  ".join(projects))
    try:
        Syncer(projects).watch(args.interval)
    except KeyboardInterrupt:
        pass


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except AttributeError:  # pythonw 등 stdout 이 없는 환경
        pass
    ap = argparse.ArgumentParser(prog="achat", description="AI 에이전트 대화를 프로젝트의 Achat/ 폴더에 Markdown 으로 저장")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="현재(또는 지정) 프로젝트 등록 + Achat/ 생성 + .gitignore 추가")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("remove", help="프로젝트 등록 해제")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(func=cmd_remove)
    sub.add_parser("list", help="등록된 프로젝트 목록").set_defaults(func=cmd_list)
    p = sub.add_parser("sync", help="한 번만 변환 (기본: 등록된 모든 프로젝트)")
    p.add_argument("path", nargs="?")
    p.set_defaults(func=cmd_sync)
    p = sub.add_parser("watch", help="실시간 감시 (기본: 등록된 모든 프로젝트)")
    p.add_argument("path", nargs="?")
    p.add_argument("--interval", type=float, default=1.0, help="확인 주기(초), 기본 1")
    p.set_defaults(func=cmd_watch)
    args = ap.parse_args(argv)
    args.func(args)
