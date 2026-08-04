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
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
CHAPTER_DIR = BASE / "public" / "chapters"
TMP_DIR = Path("/tmp/notebooklm").resolve()  # macOS /tmp 심볼릭 링크 해소 (업로드 거부 방지)
OUT_DIR = Path.home() / "Downloads"
ENV_FILE = Path.home() / "telegram-claude-bridge" / ".env"
NLM = "notebooklm"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


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
        f"# {title} — OX 퀴즈 핵심 요약",
        "",
        "이 문서는 행정법 시험 대비 OX 퀴즈 요약입니다.",
        "각 항목의 '핵심'이 시험에 나오는 올바른 법리이며,",
        "'함정'은 시험에서 틀리게 변형되어 출제되는 지문입니다.",
        "청취자가 개념과 함정 포인트를 확실히 구분하도록 설명해 주세요.",
        "",
    ]
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

    desc = "행정법 시험 대비 OX 퀴즈 요약. 옳은 법리와 함정 지문의 차이를 중심으로 한국어로 설명"
    r = run_nlm(["generate", "audio", desc, "-n", nb_id,
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
