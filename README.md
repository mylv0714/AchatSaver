# AchatSaver

AI 코딩 에이전트와 나눈 대화를 **프로젝트 안 `Achat/` 폴더에 날짜별·세션별 Markdown 으로 실시간 저장**합니다.

| 지원 도구 | 원본 위치 (자동 탐색) | 비고 |
|---|---|---|
| Claude Code | `~/.claude/projects/` (`CLAUDE_CONFIG_DIR` 지원) | 질문·답변·도구 호출·파일 수정 diff |
| Cursor (Grok 등 모든 모델) | `~/.cursor/projects/*/agent-transcripts/` | 질문·답변·도구 호출·파일 수정 diff (원본에 모델명이 없어 모델 표시는 안 됨) |
| Codex | `~/.codex/sessions/` (`CODEX_HOME` 지원) | 질문·답변·도구 호출·파일 수정 diff |

각 도구가 이미 로컬에 남기는 대화 기록을 읽어 변환하는 방식이라, 도구 쪽 설정이 필요 없습니다.
원본은 일정 기간 뒤 도구가 지우지만(Claude Code 기본 30일) `Achat/` 의 사본은 남습니다.

## 결과물

```
내프로젝트/
  .gitignore            ← "Achat/" 자동 추가 (git 에 올라가지 않음)
  Achat/
    20261008/
      1556_claude_로그인-버그-고쳐줘_1587ef.md
      2130_cursor_UI-정리해줘_be0549.md
    20261009/
      ...
```

파일 이름은 `시작시각_도구_첫질문_세션ID끝6자리.md`. 대화가 이어지는 동안 같은 파일이 계속 갱신됩니다.
도구 호출은 `🔧 Bash — 설명` 같은 한 줄 요약으로, 파일 수정(Edit·Write 등)은 `<details>` 로 접힌 diff 로 남습니다. 실행 결과는 저장하지 않습니다.

## 설치 (Windows / macOS / Linux)

Python 3.9 이상, 외부 의존성 없음.

```bash
python -m pip install --user git+https://github.com/mylv0714/AchatSaver
```

또는 이 폴더를 복사한 뒤 그 안에서 `python -m pip install --user .`

### `achat` 명령을 못 찾을 때

설치 중 `achat.exe is installed in '...\Scripts' which is not on PATH` 경고가 나왔다면, 설치 폴더가 PATH 에 없는 것입니다.
당장은 `achat` 대신 `python -m achat_saver ...` 로 똑같이 쓸 수 있고, 아래처럼 PATH 에 한 번 추가하면 `achat` 으로 쓸 수 있습니다.

**Windows** (PowerShell, 실행 후 터미널을 새로 열기):
```powershell
$d = python -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"; [Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path","User") + ";$d", "User")
```

**macOS / Linux** (`~/.zshrc` 또는 `~/.bashrc` 에 추가 후 터미널을 새로 열기):
```bash
export PATH="$(python3 -m site --user-base)/bin:$PATH"
```

## 사용법

```bash
cd 내프로젝트
achat init      # 프로젝트 등록 + Achat/ 생성 + .gitignore 추가 + 지난 대화 변환
achat watch     # 실시간 저장 (등록된 모든 프로젝트 감시, Ctrl+C 로 종료)
```

| 명령 | 설명 |
|---|---|
| `achat init [경로]` | 프로젝트 등록 (등록 목록: `~/.achat/projects.json`) |
| `achat watch [경로] [--interval 초]` | 1초마다 확인해 바뀐 세션만 다시 씀 |
| `achat sync [경로]` | 한 번만 변환하고 종료 |
| `achat list` / `achat remove [경로]` | 등록 목록 / 등록 해제 (`Achat/` 는 지우지 않음) |

## 로그인 시 자동 실행

`achat watch` 가 켜져 있어야 실시간 저장이 됩니다.

**Windows** (PowerShell, 시작프로그램에 창 없이 등록):
```powershell
$s=(New-Object -ComObject WScript.Shell).CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\achat.lnk"); $s.TargetPath=(Get-Command pythonw).Source; $s.Arguments='-m achat_saver watch'; $s.Save()
```

**macOS** — `~/Library/LaunchAgents/com.achat.watch.plist` 저장 후 `launchctl load ~/Library/LaunchAgents/com.achat.watch.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.achat.watch</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>-m</string><string>achat_saver</string><string>watch</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
```
(`/usr/bin/python3` 는 `which python3` 결과로 바꾸세요.)

**Linux** — `~/.config/systemd/user/achat.service` 저장 후 `systemctl --user enable --now achat`:
```ini
[Unit]
Description=AchatSaver

[Service]
ExecStart=/usr/bin/python3 -m achat_saver watch
Restart=on-failure

[Install]
WantedBy=default.target
```

## 주의

- 대화에는 API 키 등 민감한 내용이 섞일 수 있습니다. `Achat/` 는 기본으로 git 에서 제외됩니다.
- 각 도구의 기록 형식은 공식 문서화된 것이 아니어서 도구 업데이트로 바뀔 수 있습니다. 변환기는 `achat_saver/parsers.py` 에 도구별로 분리되어 있습니다.
- Cursor 는 원본 폴더 이름(경로를 인코딩한 이름)으로 프로젝트를 찾습니다. macOS/Linux 의 Cursor 폴더 이름 규칙은 아직 실기기 확인 전입니다.

## 개발

```bash
python -m unittest discover tests
```
