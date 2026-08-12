#!/usr/bin/env python3
"""챕터 OX 퀴즈 → 단독 강의 대본(claude CLI) → edge-tts 단일 목소리 mp3 → 텔레그램 전송.

NotebookLM 팟캐스트(두 진행자 대화형)를 대체하는 '강사 1명 모놀로그' 파이프라인.

사용법:
  python3 lecture_audio.py ch59            # 챕터 전체 강의
  python3 lecture_audio.py ch59 weak       # 오답·체크 문제만
  python3 lecture_audio.py ch59 --no-send  # 텔레그램 전송 생략

길이: 문항수 비례 (문항당 약 0.2분, 최소 5분 ~ 최대 15분)
"""
import re
import subprocess
import sys
import time
from pathlib import Path

from nlm_audio import build_summary, load_chapter, norm_chapter, telegram_send, weak_nos

OUT_DIR = Path.home() / "Downloads"
TMP_DIR = Path("/tmp/lecture_audio")
VOICE = "ko-KR-InJoonNeural"          # 차분한 남성 강의 톤
CHARS_PER_MIN = 330                    # 한국어 TTS 평균 낭독 속도
MIN_MINUTES, MAX_MINUTES = 5, 15

SCRIPT_PROMPT = """아래 자료는 행정법 시험 대비 자료입니다 (1부: 핵심 개념 요약, 2부: 기출 OX 지문).
이 자료로 강사 한 명이 혼자 낭독할 한국어 강의 대본을 작성하세요.

[반드시 지킬 규칙]
1. 인사말·자기소개·마무리 인사 금지. 첫 문장부터 바로 핵심 내용, 마지막 문장도 내용으로 끝냄.
2. 자료의 모든 주제와 모든 지문을 하나도 빠뜨리지 말고 다룰 것. 일부만 골라 요약 금지.
3. 각 지문의 근거(법리·판례 결론)는 한두 문장으로 간결하게. 같은 말 반복 금지.
4. 틀린 지문은 "시험에서 ~라고 나오면 틀린 겁니다. 옳은 표현은 ~입니다" 형태로 짝지어 대비.
5. 헷갈리는 두 개념은 "A는 ~인 반면, B는 ~입니다" 비교 형식으로.
6. 기간·요건·숫자·법령명·판례 결론은 또박또박 명시하고, 중요한 것은 한 번 더 반복.
7. 각 주제가 끝나면 "한 줄 정리." 로 시작하는 암기 문장 하나를 넣을 것.
8. 마지막은 이 챕터 최다 출제 함정 두세 가지를 다시 짚는 3~5문장으로 마무리.

[출력 형식 — TTS가 그대로 읽습니다]
- 낭독용 순수 평문만 출력. 마크다운, 특수기호(#, *, -, ① 등), 문항 번호, 소제목 표기 금지.
- 괄호·인용부호 최소화. 문장은 짧게 끊어서.
- 전체 분량은 약 {chars}자 (낭독 시 약 {mins}분). 이 분량에서 ±15% 이내로 맞출 것.
- 대본 본문 외의 설명·주석·머리말을 절대 출력하지 말 것."""


def target_minutes(n_questions: int) -> int:
    return max(MIN_MINUTES, min(MAX_MINUTES, round(n_questions * 0.2)))


def make_script(summary: str, n_q: int) -> str:
    mins = target_minutes(n_q)
    chars = mins * CHARS_PER_MIN
    prompt = SCRIPT_PROMPT.format(mins=mins, chars=chars)
    print(f"[2/5] 대본 작성 중 (목표 {mins}분 ≈ {chars}자, claude sonnet)…", flush=True)
    r = subprocess.run(
        ["claude", "-p", prompt, "--model", "sonnet"],
        input=summary, capture_output=True, text=True, timeout=1200,
    )
    script = (r.stdout or "").strip()
    if r.returncode != 0 or len(script) < 500:
        sys.exit(f"ERROR: 대본 생성 실패 — rc={r.returncode} out={script[:200]} err={r.stderr[:200]}")
    # TTS 낭독에 방해되는 잔여 기호 제거
    script = re.sub(r"[#*_`>|~\[\]]", "", script)
    script = re.sub(r"\n{3,}", "\n\n", script).strip()
    print(f"[2/5] 대본 완성: {len(script)}자", flush=True)
    return script


def make_tts(script: str, out_mp3: Path) -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    txt = TMP_DIR / (out_mp3.stem + ".txt")
    txt.write_text(script, encoding="utf-8")
    out_mp3.unlink(missing_ok=True)
    print(f"[3/5] TTS 합성 중 ({VOICE})…", flush=True)
    r = subprocess.run(
        ["edge-tts", "--voice", VOICE, "--rate", "+8%",
         "--file", str(txt), "--write-media", str(out_mp3)],
        capture_output=True, text=True, timeout=1200,
    )
    if r.returncode != 0 or not out_mp3.exists() or out_mp3.stat().st_size < 100_000:
        sys.exit(f"ERROR: TTS 실패 — {r.stderr[:300]}")


def duration_sec(mp3: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(mp3)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ch = norm_chapter(sys.argv[1])
    weak_only = "weak" in sys.argv[2:]
    send = "--no-send" not in sys.argv[2:]

    chap = load_chapter(ch)
    only = weak_nos(ch) if weak_only else None
    summary, n_q = build_summary(chap, ch, only)

    script = make_script(summary, n_q)
    mode = "오답" if weak_only else "전체"
    out_mp3 = OUT_DIR / f"행정법_{ch}_{mode}강의.mp3"
    make_tts(script, out_mp3)

    dur = duration_sec(out_mp3)
    print(f"[4/5] 합성 완료: {out_mp3.name} ({dur/60:.1f}분, {out_mp3.stat().st_size // 1024}KB)", flush=True)
    if dur < 60:
        sys.exit("ERROR: 오디오가 1분 미만 — 대본/TTS 이상")

    if send:
        telegram_send(out_mp3, f"🎙️ {chap.get('title', ch)} — {mode} 단독 강의 ({dur/60:.0f}분, {n_q}문항)")
    print(f"[5/5] DONE {out_mp3}", flush=True)


if __name__ == "__main__":
    main()
