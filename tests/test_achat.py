import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from achat_saver.parsers import parse_claude, parse_codex, parse_cursor
from achat_saver.render import fence, render
from achat_saver.watcher import JsonlTail, Syncer, is_inside

PROJECT = os.path.abspath("proj_root")

CLAUDE = [
    {"type": "user", "sessionId": "abc123", "cwd": PROJECT, "timestamp": "2026-10-08T01:00:00Z",
     "message": {"role": "user", "content": "파일 목록 보여줘"}},
    {"type": "user", "isMeta": True, "timestamp": "2026-10-08T01:00:00Z",
     "message": {"role": "user", "content": "메타 메시지"}},
    {"type": "assistant", "timestamp": "2026-10-08T01:00:05Z",
     "message": {"model": "claude-opus-5-5", "content": [
         {"type": "thinking", "thinking": "숨김"},
         {"type": "text", "text": "확인할게요."},
         {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls", "description": "List files"}}]}},
    {"type": "user", "timestamp": "2026-10-08T01:00:06Z",
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "a.py\nb.py"}]}},
    {"type": "assistant", "timestamp": "2026-10-08T01:00:07Z", "message": {"content": [
        {"type": "tool_use", "id": "t2", "name": "Edit", "input": {
            "file_path": os.path.join(PROJECT, "src", "a.py"), "old_string": "x = 1\ny = 2", "new_string": "x = 1\ny = 3"}}]}},
    {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "서브에이전트"}]}},
    {"type": "ai-title", "aiTitle": "파일 목록"},
]

CURSOR = [
    {"role": "user", "message": {"content": [{"type": "text", "text":
        "<timestamp>Tuesday, Sep 8, 2026, 9:07 PM (UTC+9)</timestamp>\n<user_query>\n안녕\n</user_query>"}]}},
    {"role": "assistant", "message": {"content": [
        {"type": "text", "text": "검색해볼게요"},
        {"type": "tool_use", "name": "WebSearch", "input": {"search_term": "온수역"}}]}},
    {"type": "turn_ended", "status": "success"},
]

CODEX = [
    {"timestamp": "2026-09-08T13:43:58Z", "type": "session_meta", "payload": {"id": "s-1", "cwd": PROJECT}},
    {"timestamp": "2026-09-08T13:43:58Z", "type": "turn_context", "payload": {"model": "gpt-5"}},
    {"timestamp": "2026-09-08T13:43:58Z", "type": "response_item",
     "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>…"}]}},
    {"timestamp": "2026-09-08T13:43:59Z", "type": "event_msg", "payload": {"type": "user_message", "message": "분석해줘"}},
    {"timestamp": "2026-09-08T13:43:59Z", "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "UserMessage", "content": [{"type": "text", "text": "분석해줘"}]}}},
    {"timestamp": "2026-09-08T13:44:01Z", "type": "response_item",
     "payload": {"type": "function_call", "name": "shell", "call_id": "c1", "arguments": "{\"command\": [\"ls\"]}"}},
    {"timestamp": "2026-09-08T13:44:02Z", "type": "response_item",
     "payload": {"type": "function_call_output", "call_id": "c1", "output": "{\"output\": \"x.js\"}"}},
    {"timestamp": "2026-09-08T13:44:03Z", "type": "response_item",
     "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "끝"}]}},
    {"timestamp": "2026-09-08T13:44:04Z", "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "FileChange", "changes": {os.path.join(PROJECT, "new.md"): {"type": "add", "content": "# 제목\n본문"}}}}},
]


class ParserTests(unittest.TestCase):
    def test_claude(self):
        s = parse_claude(CLAUDE)
        self.assertEqual((s.id, s.model, s.title), ("abc123", "claude-opus-5-5", "파일 목록"))
        self.assertEqual([(e.role, e.text or e.tool) for e in s.events],
                         [("user", "파일 목록 보여줘"), ("assistant", "확인할게요."), ("assistant", "Bash"),
                          ("assistant", "Edit")])

    def test_cursor(self):
        s = parse_cursor(CURSOR)
        self.assertEqual(s.first_prompt, "안녕")
        self.assertEqual(s.start.isoformat(), "2026-09-08T21:07:00+09:00")
        self.assertEqual(s.events[2].tool, "WebSearch")

    def test_codex_skips_injected_and_duplicate_user_messages(self):
        s = parse_codex(CODEX)
        self.assertEqual([e.text or e.tool for e in s.events], ["분석해줘", "shell", "끝", "FileChange"])
        self.assertEqual((s.model, s.cwd), ("gpt-5", PROJECT))
        self.assertEqual(s.events[3].diff, "+# 제목\n+본문")


class RenderTests(unittest.TestCase):
    def test_render_one_line_tools_and_folded_diff(self):
        md = render(parse_claude(CLAUDE))
        self.assertIn("# 파일 목록", md)
        self.assertIn("- 🔧 **Bash** `List files`", md)
        self.assertNotIn("a.py\nb.py", md)  # 실행 결과는 저장하지 않음
        self.assertIn(f"<b>Edit</b> — {os.path.join('src', 'a.py')} (+1 −1)</summary>", md)
        self.assertIn("```diff\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3\n```", md)
        self.assertNotIn("숨김", md)
        self.assertNotIn("서브에이전트", md)

    def test_fence_survives_backticks(self):
        self.assertTrue(fence("```py\nx\n```").startswith("````\n"))

    def test_git_bash_path(self):
        if os.name == "nt":
            self.assertTrue(is_inside("/d/Fun/X/sub", Path(r"D:\Fun\X")))
        self.assertFalse(is_inside(PROJECT + "2", Path(PROJECT)))


class TailTests(unittest.TestCase):
    def test_partial_line_waits_for_newline(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jsonl"
            p.write_bytes(b'{"a": 1}\n{"b"')
            t = JsonlTail(p)
            self.assertTrue(t.update())
            self.assertEqual(t.records, [{"a": 1}])
            with open(p, "ab") as f:
                f.write(b': 2}\n')
            self.assertTrue(t.update())
            self.assertEqual(t.records, [{"a": 1}, {"b": 2}])
            self.assertFalse(t.update())


class EndToEndTests(unittest.TestCase):
    def test_sync_writes_dated_files_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as home:
            home = Path(home)
            project = home / "My Proj"
            project.mkdir()
            cl = [dict(r, cwd=str(project)) if "cwd" in r else r for r in CLAUDE]
            cx = [dict(r, payload=dict(r["payload"], cwd=str(project))) if r["type"] == "session_meta" else r
                  for r in CODEX]
            enc = re.sub(r"[^A-Za-z0-9]", "-", str(project))  # 각 도구가 쓰는 경로 인코딩 흉내
            files = {
                home / ".claude/projects" / enc / "abc123.jsonl": cl,
                home / ".cursor/projects" / enc.strip("-") / "agent-transcripts/cur1/cur1.jsonl": CURSOR,
                home / ".codex/sessions/2026/09/08/rollout-x.jsonl": cx,
            }
            for path, recs in files.items():
                path.parent.mkdir(parents=True)
                path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
            env = {"HOME": str(home), "USERPROFILE": str(home), "CLAUDE_CONFIG_DIR": "", "CODEX_HOME": ""}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(Syncer([project]).step(log=lambda *_: None), 3)
                self.assertEqual(Syncer([project]).step(log=lambda *_: None), 0)  # 내용이 같으면 다시 쓰지 않음
            names = sorted(p.relative_to(project / "Achat").as_posix() for p in (project / "Achat").rglob("*.md"))
            self.assertEqual(len(names), 3)
            self.assertTrue(any(n.startswith("20260908/2107_cursor_안녕_") for n in names), names)
            self.assertTrue(all(len(n.split("/")[0]) == 8 for n in names))


if __name__ == "__main__":
    unittest.main()
