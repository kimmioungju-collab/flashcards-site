#!/usr/bin/env python3
"""소방공무원법(fs) 챕터 → NotebookLM 단독 강의 mp3 → Whisper 자막 → 핵심요약 섹션 정렬.
출력: public/audio/nlm/<ch>_full.{mp3,script.json,sections.json}  (앱 라이브 하이라이트용)
사용: python3 fs_nlm.py fs01
"""
import sys
import time
from pathlib import Path

import nlm_audio as na
import pod_align
import pod_script

SUBJECT = "소방공무원법"
OUT_DIR = Path(__file__).resolve().parent / "public" / "audio" / "nlm"


def main() -> None:
    ch = sys.argv[1].strip().lower()
    key = f"{ch}_full"
    na.STUDY_PROMPT = na.STUDY_PROMPT.replace("행정법", SUBJECT)
    chap = na.load_chapter(ch)
    title = chap.get("title", ch)
    chunks = na.split_chunks(na.filter_questions(chap, None))
    n = len(chunks)
    print(f"[{ch}] {sum(map(len, chunks))}문항 → {n}편", flush=True)
    na.TMP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%m%d-%H%M")
    parts = []
    for i, qs in enumerate(chunks, 1):
        src = na.TMP_DIR / f"{SUBJECT}_{ch}_{i}부.txt"
        src.write_text(na.build_chunk_summary(title, ch, qs, i, n), encoding="utf-8")
        mp3 = na.TMP_DIR / f"{ch}_full_{i}.mp3"
        if mp3.exists() and mp3.stat().st_size > 10_000:
            print(f"[{ch}] {i}/{n}편 기존 파일 재사용", flush=True)
        else:
            na.make_audio(src, f"{SUBJECT} {ch} {i}-{n} {stamp}", mp3, i, n)
        parts.append(mp3)
    out = OUT_DIR / f"{key}.mp3"
    na.concat_mp3(parts, out)
    print(f"[{ch}] 병합 완료 {out.stat().st_size // 1024}KB", flush=True)
    pod_script.make(key)
    pod_align.make(key)
    print(f"DONE {key}", flush=True)


if __name__ == "__main__":
    main()
