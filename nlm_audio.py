#!/usr/bin/env python3
"""챕터 OX 퀴즈 요약 → NotebookLM 오디오 개요(팟캐스트) 생성 → 텔레그램 전송.

사용법:
  python3 nlm_audio.py ch05          # 챕터 전체 요약 오디오
  python3 nlm_audio.py ch05 weak     # 오답·체크 문제만 요약 오디오
  python3 nlm_audio.py ch05 --no-send  # 텔레그램 전송 생략

흐름: 챕터 JSON → 요약 텍스트(/tmp/notebooklm/) → notebooklm CLI로
노트북 생성·소스 업로드·한국어 오디오 생성(--wait) → mp3 다운로드 → 텔레그램 전송.
소요 시간: 오디오 생성에 보통 5~15분 (반드시 백그라운드로 실행할 것).
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
CHAPTER_DIR = BASE / "public" / "chapters"
INTRO_DIR = BASE / "public" / "intros"     # 앱에 이미 만들어 둔 챕터 핵심 요약
TMP_DIR = Path("/tmp/notebooklm").resolve()  # macOS /tmp 심볼릭 링크 해소 (업로드 거부 방지)
OUT_DIR = Path.home() / "Downloads"
ENV_FILE = Path.home() / "telegram-claude-bridge" / ".env"
NLM = shutil.which("notebooklm") or "/opt/homebrew/bin/notebooklm"  # 크론 환경 PATH 대비
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# 오디오 스타일: brief=요약 브리핑(군더더기 없이 핵심만), length=default(약 10분 내외)
AUDIO_FORMAT = "brief"
AUDIO_LENGTH = "default"

# 공부용 진행 지침 — 이동 중 듣기만 해도 정리가 되도록 설계
STUDY_PROMPT = """이 자료는 행정법 객관식·OX 시험 대비 기출 지문 모음입니다.
수험생이 이동 중 귀로만 듣고 정리하는 용도이므로, 아래 지침을 반드시 지켜 한국어로 진행해 주세요.

[진행 방식]
1. 인사말·자기소개·잡담·소스 소개는 생략하고 첫 문장부터 바로 핵심 개념으로 들어갑니다.
2. 자료 1부('핵심 개념 요약')를 먼저 차례대로 설명해 뼈대를 세운 뒤,
   2부('기출 OX 지문')로 넘어가 그 개념이 시험에서 어떻게 함정으로 나오는지 확인시켜 줍니다.
   1부의 소제목 순서와 강조점을 그대로 따르고, 임의로 순서를 바꾸지 않습니다.
3. 주제 단위로 묶어서, 각 주제마다 이 순서를 지킵니다.
   ① 개념을 한 문장으로 정의 → ② 시험에 나오는 함정 → ③ 정답을 가르는 판단 기준.
4. 문항 번호는 읽지 말고, 내용 중심으로 자연스럽게 이어서 설명합니다.

[가장 중요 — 함정 처리]
4. 틀린 지문(함정)은 반드시 "이렇게 나오면 틀린다 → 옳은 표현은 이것이다" 형태로
   잘못된 표현과 올바른 표현을 짝지어 대비시켜 말합니다.
5. 자주 바꿔치기되는 단어(전부/일부, ~할 수 있다/~하여야 한다, 기속/재량, 취소/무효,
   처분성 인정/부정 등)는 어느 쪽이 정답인지 분명히 못 박아 줍니다.

[암기 지원]
6. 헷갈리는 두 개념은 "A는 ~인 반면, B는 ~이다" 비교 형식으로 나란히 설명합니다.
7. 기간·요건·숫자와 법령·판례 명칭은 또박또박 말하고 한 번 더 반복해 강조합니다.
8. 각 주제가 끝날 때마다 "한 줄 정리:"로 시작하는 암기 문장을 한 문장으로 남깁니다.
9. 마지막에는 이 챕터에서 시험에 나올 확률이 높은 핵심을 3~5문장으로 정리하고,
   특히 자주 틀리는 함정 두세 가지를 다시 짚어 주며 마무리합니다.

[말하기 규칙]
10. "보시다시피", "화면에서" 같은 시각 자료 표현은 절대 쓰지 않습니다.
11. 근거 없는 추측이나 자료에 없는 내용은 덧붙이지 않습니다.
12. 속도는 차분하게, 문장은 짧게 끊어 말합니다."""


def norm_chapter(raw: str) -> str:
    ch = raw.strip().lower()
    if not ch.startswith("ch"):
        ch = "ch" + ch
    return "ch" + ch[2:].zfill(2)


def load_chapter(ch: str) -> dict:
    path = CHAPTER_DIR / f"{ch}.json"
    if not path.exists():
        sys.exit(f"ERROR: 챕터 파일 없음 — {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def intro_text(ch: str) -> str:
    """앱의 '이 챕터 핵심 요약'(intros/chNN.json) HTML을 낭독용 평문으로 변환."""
    path = INTRO_DIR / f"{ch}.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = (data.get("intro") or data.get("introEasy") or "").strip()
    if not raw:
        return ""
    text = re.sub(r"<(h[1-6])[^>]*>", r"\n\n### ", raw)          # 소제목
    text = re.sub(r"<(p|div|li|tr)[^>]*>", "\n", text)            # 문단/항목 줄바꿈
    text = re.sub(r"</(td|th)>\s*<(td|th)[^>]*>", " · ", text)    # 표 셀 구분
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)                           # 남은 태그 제거
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def weak_nos(ch: str) -> set[str]:
    """weak_report.py 로 오답·체크 문제 번호 조회."""
    r = subprocess.run(
        [sys.executable, str(BASE / "weak_report.py"), ch],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        sys.exit(f"ERROR: weak_report 실패 — {r.stdout[:200]}{r.stderr[:200]}")
    data = json.loads(r.stdout)
    return {w["no"] for w in data.get("weak", [])}


def build_summary(chap: dict, ch: str, only_nos: set[str] | None) -> str:
    """주제별로 묶은 OX 요약 텍스트 생성 (팟캐스트 소스용)."""
    title = chap.get("title", ch)
    lines = [
        f"# {title}",
        "",
        "이 문서는 두 부분으로 되어 있습니다.",
        "1부는 이 챕터의 '핵심 개념 요약'이고, 2부는 실제 기출 OX 지문입니다.",
        "1부의 개념을 먼저 설명한 뒤, 2부의 지문으로 함정을 확인시켜 주세요.",
        "2부에서 '핵심'은 시험에 나오는 올바른 법리이고,",
        "'함정'은 시험에서 틀리게 변형되어 출제되는 지문입니다.",
        "",
    ]

    intro = intro_text(ch)
    if intro:
        lines += ["", "=" * 50, "# 1부 · 핵심 개념 요약 (먼저 설명할 내용)", "=" * 50, "", intro, ""]
        print(f"[1/5] 기존 요약본 포함: {len(intro)}자", flush=True)
    else:
        print("[1/5] 요약본 없음 — 기출 지문만 사용", flush=True)
    lines += ["", "=" * 50, "# 2부 · 기출 OX 지문 (함정 확인용)", "=" * 50]
    current_theme = None
    count = 0
    for q in chap.get("questions", []):
        no = str(q.get("no", ""))
        if only_nos is not None and no not in only_nos:
            continue
        theme = q.get("theme", "") or "기타"
        if theme != current_theme:
            lines += ["", f"## {theme}", ""]
            current_theme = theme
        ans = (q.get("ans") or "").strip()
        text = (q.get("q") or "").strip()
        exp = (q.get("exp") or "").strip()
        if ans == "O":
            lines.append(f"({no}) [핵심 — 옳은 내용] {text}")
            if exp:
                lines.append(f"    보충: {exp}")
        elif ans == "X":
            lines.append(f"({no}) [함정 — 틀린 지문] {text}")
            if exp:
                lines.append(f"    → 올바른 법리: {exp}")
        else:  # 객관식 ①~⑤
            lines.append(f"({no}) [객관식] {text}")
            lines.append(f"    정답: {ans}")
            if exp:
                lines.append(f"    해설: {exp}")
        lines.append("")
        count += 1
    if count == 0:
        sys.exit("ERROR: 요약할 문제가 없습니다 (오답·체크 0건일 수 있음)")
    print(f"[1/5] 요약 생성: {count}문항", flush=True)
    return "\n".join(lines)


def run_nlm(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run([NLM] + args, capture_output=True, text=True, timeout=timeout)


def make_audio(src_file: Path, nb_title: str, out_mp3: Path) -> None:
    r = run_nlm(["create", nb_title], timeout=120)
    m = UUID_RE.search(r.stdout + r.stderr)
    if not m:
        sys.exit(f"ERROR: 노트북 생성 실패 — {(r.stdout + r.stderr)[:300]}")
    nb_id = m.group(0)
    print(f"[2/5] 노트북 생성: {nb_id[:8]}…", flush=True)

    r = run_nlm(["source", "add", str(src_file), "-n", nb_id], timeout=300)
    if r.returncode != 0:
        sys.exit(f"ERROR: 소스 업로드 실패 — {(r.stdout + r.stderr)[:300]}")
    print("[3/5] 소스 업로드 완료, 인덱싱 대기 10초", flush=True)
    time.sleep(10)

    r = run_nlm(["generate", "audio", STUDY_PROMPT, "-n", nb_id,
                 "--format", AUDIO_FORMAT, "--length", AUDIO_LENGTH,
                 "--language", "ko", "--wait", "--retry", "3"], timeout=2400)
    if r.returncode != 0:
        sys.exit(f"ERROR: 오디오 생성 실패 — {(r.stdout + r.stderr)[:300]}")
    print("[4/5] 오디오 생성 완료", flush=True)

    r = run_nlm(["download", "audio", str(out_mp3), "-n", nb_id], timeout=300)
    if not out_mp3.exists() or out_mp3.stat().st_size < 10_000:
        sys.exit(f"ERROR: 다운로드 실패 — {(r.stdout + r.stderr)[:300]}")
    print(f"[5/5] 다운로드 완료: {out_mp3} ({out_mp3.stat().st_size // 1024}KB)", flush=True)


def telegram_send(mp3: Path, caption: str) -> None:
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = (env.get("ALLOWED_USERS", "").split(",") or [""])[0]
    if not token or not chat_id:
        print("WARN: 텔레그램 설정 없음 — 전송 생략", flush=True)
        return
    r = subprocess.run(
        ["curl", "-s", f"https://api.telegram.org/bot{token}/sendAudio",
         "-F", f"chat_id={chat_id}", "-F", f"audio=@{mp3}",
         "-F", f"caption={caption}"],
        capture_output=True, text=True, timeout=300,
    )
    ok = '"ok":true' in r.stdout
    print(f"텔레그램 전송: {'✅ 성공' if ok else '❌ 실패 ' + r.stdout[:200]}", flush=True)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ch = norm_chapter(sys.argv[1])
    weak_only = "weak" in sys.argv[2:]
    send = "--no-send" not in sys.argv[2:]

    chap = load_chapter(ch)
    only = weak_nos(ch) if weak_only else None
    summary = build_summary(chap, ch, only)

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    mode = "오답" if weak_only else "전체"
    src_file = TMP_DIR / f"행정법_{ch}_{mode}_요약.txt"
    src_file.write_text(summary, encoding="utf-8")

    stamp = time.strftime("%m%d-%H%M")
    nb_title = f"행정법OX {ch} {mode} {stamp}"
    out_mp3 = OUT_DIR / f"행정법_{ch}_{mode}요약.mp3"
    make_audio(src_file, nb_title, out_mp3)

    if send:
        telegram_send(out_mp3, f"🎧 {chap.get('title', ch)} — {mode} OX 요약 오디오 (NotebookLM)")
    print(f"DONE {out_mp3}", flush=True)


if __name__ == "__main__":
    main()
