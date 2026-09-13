#!/usr/bin/env python3
"""--pod: 3인 대화형 팟캐스트 대본(김강사·초보자·진행자, 사용자 Gemini 프롬프트 2026-09-13) → 화자별 Airy TTS 음성(POD_TTS=edge 로 edge-tts 폴백) → 앱용 오디오 세트.
lecture_pod.py 의 --coach(1인 낭독)와 같은 출력 형식(<key>_full.{mp3,sections.json,script.json})을 만든다.

사용법:
  python3 pod_dialog.py fs01            # 대본(claude) + TTS → public/audio/nlm/fs01_full.*
  python3 pod_dialog.py fs01 --dry      # 대본만 (/tmp/lecture_pod/fs01_pod/script.txt)
  python3 pod_dialog.py fs01 --reuse    # 저장된 대본 재사용 (음성만 다시)
"""
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import edge_tts

from lecture_pod import (AIRY_MAX_CHARS, CLAUDE_MODEL, GAP_SEC, MIN_SEC_PER_CHAR, OUT_DIR, TMP_ROOT, TTS_RETRY,
                         airy_synth, build_coach_material, clean_for_tts, concat, duration, load_sections, log,
                         norm_chapter, target_minutes)
from nlm_audio import load_chapter

SPEAKERS = ("김강사", "초보자", "진행자")
TTS_ENGINE = os.environ.get("POD_TTS", "airy")     # airy(기본, 사용자 지시 2026-09-13 "엣지 말고 그 전에 쓰던 것") | edge
# 화자별 Airy 음성 id (목록: https://api.airy.so/v1/studio/voices?lang=ko)
AIRY_STYLE = os.environ.get("POD_AIRY_STYLE", "bright")   # 사용자 지시 2026-09-13 "밝은 목소리로"
AIRY_SPEED = float(os.environ.get("POD_AIRY_SPEED", "1.1"))   # 사용자 지시 "기본보다 살짝 빠르게"
AIRY_VOICES = {
    "김강사": os.environ.get("POD_V_KIM", "a6fe69257256aa70"),   # Eric — 남성 (사용자 선택 2026-09-13)
    "초보자": os.environ.get("POD_V_NEW", "d04f9d34c04dce73"),   # Leo — 남성 (사용자 선택)
    "진행자": os.environ.get("POD_V_MC", "608e2509506454b2"),    # Mary — 여성 (사용자 선택)
}
# 화자별 edge-tts 음성 (POD_TTS=edge 폴백용)
VOICES = {
    "김강사": {"voice": "ko-KR-InJoonNeural", "rate": "+4%", "pitch": "-2Hz"},            # 남성, 자신감 있는 1타 강사
    "초보자": {"voice": "ko-KR-SunHiNeural", "rate": "+10%", "pitch": "+6Hz"},            # 여성, 열정적 수험생
    "진행자": {"voice": "ko-KR-HyunsuMultilingualNeural", "rate": "+6%", "pitch": "+0Hz"},  # 남성, MC
}
LINE_GAP = 0.25                 # 대사 사이 무음(초)
CHARS_PER_MIN = 300             # 대화체 낭독 속도
PARALLEL = 4
LINE_RE = re.compile(r"^\s*\[?\s*(김강사|초보자|진행자)\s*\]?\s*[:：]\s*(.*)$")

POD_PROMPT = """[역할 부여]
당신은 소방승진 및 소방공무원법 전문 교육 크리에이터입니다. 제공된 요약본 텍스트를 바탕으로, 3명의 캐릭터가 대화하며 청취자가 자연스럽게 핵심을 암기할 수 있는 오디오 팟캐스트 대본을 작성해 주세요.

[캐릭터 설정 (3명)]
1. [김강사]: 메인 1타 강사. 출제 포인트와 핵심 두음법칙(암기법)을 명쾌하고 자신감 있게 던져주는 역할.
2. [초보자]: 열정적이지만 자주 헷갈려 하는 수험생. 시청자를 대신해 질문하고, 강사의 두음법칙을 중얼거리며 따라 외우는 역할.
3. [진행자]: 전체 흐름을 잡는 MC 겸 퀴즈 마스터. 섹션을 넘기거나 마지막에 리마인드 퀴즈를 출제하는 역할.

[대본 작성 규칙]
- 출력 형식: 파이썬 정규식으로 쉽게 분리할 수 있도록 반드시 `[캐릭터이름]: 대사` 형태로 한 줄에 한 대사씩 작성하세요. (예: `[김강사]: 이번 출제 포인트는...`)
- 도입부 최소화: 불필요한 날씨나 일상 인사는 생략하고, 곧바로 해당 챕터의 핵심 출제 포인트로 진입하세요.
- 티키타카 구조: [김강사]가 개념을 던지면 -> [초보자]가 오해하거나 질문하고 -> [김강사]가 다시 정확한 숫자나 두음법칙으로 교정해 주는 패턴을 적극 활용하세요.
- 반복 효과: 두음법칙(예: 임교복신, 제총대 등)과 시험에 자주 나오는 '핵심 숫자(일수, 연도 등)'는 대화 중에 톤을 높이거나 2번 이상 반복해서 말하도록 연출하세요.
- 마무리 퀴즈: 대본 마지막은 항상 [진행자]가 주도하여 빠르고 경쾌하게 OX 또는 단답형 리마인드 퀴즈 3~5개를 내고, [초보자]가 맞추는 형태로 끝내세요.
- 내용 누락 금지: [기본 요약]의 수치, 조건, 주체, 핵심 개념은 100% 대화 속에 반영하세요.

[앱 연동 형식 — 반드시 준수]
- [기본 요약]은 [[SEC n]] 으로 나뉜 섹션 목록입니다. 학생은 화면에서 해당 섹션을 보며 듣습니다.
- 섹션마다 반드시 `[[SEC n]]` 한 줄(n은 섹션 번호, 0부터)을 먼저 쓰고, 그 아래에 그 섹션의 대사들을 씁니다.
- 섹션 번호 0부터 {last}까지 **빠짐없이, 순서대로** 모두 출력합니다. 하나라도 빠지면 안 됩니다.
- 섹션 전환은 [진행자] 대사로 자연스럽게. 리마인드 퀴즈는 마지막 섹션 [[SEC {last}]] 끝에 넣습니다.
- 대사는 TTS로 읽히므로 낭독용 순수 평문만. 마크다운·특수기호(#, *, -, ①, →, ·, ~, §)·지문(웃음) 금지. 말줄임표는 "..." 로.
- 법령 조문은 "제36조 제1항"처럼 말로 풀어서. 숫자는 "60일", "3퍼센트"처럼 읽히게. O와 X는 "오", "엑스"로.
- 대본 외 설명·주석 절대 금지.
- 전체 분량은 약 {mins}분(약 {chars}자) 안팎이지만, 내용 100% 반영이 분량보다 우선합니다.
"""


def split_raw_sections(raw: str, n_sec: int) -> list[str] | None:
    parts = re.split(r"^\s*\[\[SEC\s*(\d+)\]\]\s*$", raw, flags=re.M)
    found = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2) if parts[i].isdigit()}
    missing = [i for i in range(n_sec) if not found.get(i)]
    if missing:
        log(f"대본 섹션 누락: {missing}")
        return None
    return [found[i] for i in range(n_sec)]


def parse_lines(section: str) -> list[tuple[str, str]]:
    """섹션 원문 → [(화자, 대사)]. 화자 표기 없는 줄은 직전 화자에 이어 붙인다."""
    lines: list[tuple[str, str]] = []
    for ln in section.splitlines():
        if not ln.strip():
            continue
        m = LINE_RE.match(ln)
        if m:
            lines.append((m.group(1), m.group(2).strip()))
        elif lines:
            sp, prev = lines[-1]
            lines[-1] = (sp, f"{prev} {ln.strip()}")
    return [(sp, clean_for_tts(t)) for sp, t in lines if clean_for_tts(t)]


def make_script(material: str, n_sec: int, mins: int) -> list[str]:
    prompt = POD_PROMPT.format(last=n_sec - 1, chars=mins * CHARS_PER_MIN, mins=mins)
    for attempt in range(1, 3):
        log(f"팟캐스트 대본 작성 중 (목표 {mins}분, 섹션 {n_sec}개, claude {CLAUDE_MODEL}, {attempt}회차)")
        r = subprocess.run(["claude", "-p", prompt, "--model", CLAUDE_MODEL],
                           input=material, capture_output=True, text=True, timeout=1500)
        secs = split_raw_sections(r.stdout or "", n_sec) if r.returncode == 0 else None
        if secs and all(parse_lines(s) for s in secs):
            log(f"대본 완성: {sum(map(len, secs))}자")
            return secs
        log(f"대본 실패 rc={r.returncode} err={(r.stderr or '')[:200]}")
    sys.exit("ERROR: 대본 생성 2회 실패")


async def synth_one(speaker: str, text: str, out: Path) -> None:
    if TTS_ENGINE == "airy":
        if len(text) > AIRY_MAX_CHARS:
            raise RuntimeError(f"Airy {AIRY_MAX_CHARS}자 초과 대사: {len(text)}자")
        await airy_synth(text, out, voice=AIRY_VOICES[speaker], style=AIRY_STYLE, speed=AIRY_SPEED)
        return
    v = VOICES[speaker]
    await edge_tts.Communicate(text, v["voice"], rate=v["rate"], pitch=v["pitch"]).save(str(out))


async def tts_line(speaker: str, text: str, out: Path, sem: asyncio.Semaphore) -> float:
    async with sem:
        for attempt in range(1, TTS_RETRY + 1):
            try:
                out.unlink(missing_ok=True)
                await synth_one(speaker, text, out)
                d = duration(out) if out.exists() else 0.0
                if d >= len(text) * MIN_SEC_PER_CHAR:
                    return d
                log(f"  합성 결과 의심({d:.1f}s/{len(text)}자, {speaker}) → 재시도 {attempt}")
            except Exception as e:
                log(f"  합성 오류({attempt}, {speaker}): {str(e)[:160]}")
            await asyncio.sleep(1.5 * attempt)
    raise RuntimeError(f"TTS {TTS_RETRY}회 실패: [{speaker}] {text[:40]}…")


def make_silence(path: Path, sec: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", str(sec), "-c:a", "libmp3lame", "-b:a", "48k", str(path)], check=True)


async def synth_all(sections: list[list[tuple[str, str]]], tmp: Path) -> tuple[list[Path], list[dict], list[dict]]:
    gap, line_gap = tmp / "gap.mp3", tmp / "linegap.mp3"
    make_silence(gap, GAP_SEC)
    make_silence(line_gap, LINE_GAP)
    gap_len, line_len = duration(gap) or GAP_SEC, duration(line_gap) or LINE_GAP
    sem = asyncio.Semaphore(PARALLEL)
    files, tl, script = [], [], []
    t, done, total = 0.0, 0, sum(map(len, sections))
    log(f"대사 {total}개")
    for i, lines in enumerate(sections):
        start = t
        outs = [tmp / f"s{i:02d}_{j:03d}.mp3" for j in range(len(lines))]
        durs = await asyncio.gather(*(tts_line(sp, tx, f, sem) for (sp, tx), f in zip(lines, outs)))
        for (sp, tx), f, d in zip(lines, outs, durs):
            files.append(f)
            script.append({"s": round(t, 2), "e": round(t + d, 2), "t": f"{sp}: {tx}", "sp": sp})
            t += d
            files.append(line_gap)
            t += line_len
            done += 1
        log(f"  TTS {done}/{total} (섹션 {i}, {sum(durs):.1f}s)")
        tl.append({"s": round(start, 2), "e": round(t, 2), "i": i})
        if i < len(sections) - 1:
            files.append(gap)
            t += gap_len
    return files, tl, script


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ch = norm_chapter(sys.argv[1])
    dry, reuse = "--dry" in sys.argv, "--reuse" in sys.argv
    key = f"{ch}_full"
    chap, secs = load_chapter(ch), load_sections(ch)
    tmp = TMP_ROOT / f"{ch}_pod"
    tmp.mkdir(parents=True, exist_ok=True)
    saved = tmp / "script.txt"
    raw_secs = split_raw_sections(saved.read_text(encoding="utf-8"), len(secs)) if reuse and saved.exists() else None
    if raw_secs:
        log(f"저장된 대본 재사용: {saved}")
    else:
        raw_secs = make_script(build_coach_material(secs), len(secs), target_minutes(len(chap["questions"]), len(secs)))
        saved.write_text("\n\n".join(f"[[SEC {i}]]\n{s}" for i, s in enumerate(raw_secs)), encoding="utf-8")
    lines = [parse_lines(s) for s in raw_secs]
    if dry:
        log(f"DRY — 대본만 저장: {saved} (대사 {sum(map(len, lines))}개)")
        return
    files, tl, script = asyncio.run(synth_all(lines, tmp))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mp3 = OUT_DIR / f"{key}.mp3"
    concat(files, mp3)
    total = duration(mp3)
    if total < 60:
        sys.exit(f"ERROR: 결과 오디오가 {total:.0f}초 — 이상")
    (OUT_DIR / f"{key}.sections.json").write_text(
        json.dumps({"sections": [{"t": s["t"], "h": s["h"]} for s in secs], "tl": tl}, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / f"{key}.script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    log(f"DONE {mp3.name} {total/60:.1f}분 {mp3.stat().st_size//1024}KB, 섹션 {len(tl)}, 대사 {len(script)}")


if __name__ == "__main__":
    main()
