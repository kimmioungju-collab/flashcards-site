"""위원회 유치원 선생님 대본(public/audio/committees_teacher.txt) → TTS(lecture_pod 백엔드: Airy, 폴백 edge) → mp3 + 텔레그램 전송.
  python3 committees_audio.py            # 백그라운드 실행 권장 (10여 분)
"""
import asyncio, json, re, shutil, sys
from pathlib import Path
import lecture_pod as LP
from nlm_audio import telegram_send

BASE = Path(__file__).resolve().parent
SRC = BASE / "public" / "audio" / "committees_teacher_script.txt"   # 대본 원본 (concat 목록이 .txt를 덮어쓰지 않도록 이름 분리)
OUT = BASE / "public" / "audio" / "committees_teacher.mp3"
TL = BASE / "public" / "audio" / "committees_teacher.json"
TMP = Path("/tmp/committees_audio"); TMP.mkdir(exist_ok=True)
MAX_CHARS = 180   # 한 번에 합성할 길이 (길면 중간 발음이 뭉개짐)


def chunks_of(section: str) -> list[str]:
    sents = [s.strip() for s in re.split(r"(?<=[.?!])\s+", section.strip()) if s.strip()]
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) + 1 > MAX_CHARS:
            out.append(cur); cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


async def main() -> None:
    sections = [s for s in SRC.read_text(encoding="utf-8").split("\n\n") if s.strip()]
    plans = [chunks_of(s) for s in sections]
    total = sum(map(len, plans))
    LP.log(f"섹션 {len(sections)}개 / 청크 {total}개 / {sum(map(len, sections))}자")
    gap = TMP / "gap.mp3"; LP.make_silence(gap); gap_len = LP.duration(gap)
    files, tl, t, done = [], [], 0.0, 0
    for i, chunks in enumerate(plans):
        outs = [TMP / f"s{i:02d}_{j:03d}.mp3" for j in range(len(chunks))]
        durs = await asyncio.gather(*(LP.tts_chunk(c, f) for c, f in zip(chunks, outs)))
        for c, f, d in zip(chunks, outs, durs):
            files.append(f); tl.append({"s": round(t, 2), "e": round(t + d, 2), "t": c}); t += d; done += 1
        LP.log(f"  TTS {done}/{total} (섹션 {i}, {sum(durs):.1f}s)")
        if i < len(plans) - 1:
            files.append(gap); t += gap_len
    final = TMP / "final.mp3"
    LP.concat(files, final)          # concat은 final.txt에 목록을 쓰므로 OUT(.txt=대본)과 분리
    shutil.copy(final, OUT)
    shutil.copy(SRC, OUT.with_suffix(".txt"))
    TL.write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
    dur = LP.duration(OUT)
    dl = Path.home() / "Downloads" / "소방공무원법_위원회_선생님강의.mp3"
    shutil.copy(OUT, dl)
    LP.log(f"완료: {OUT} ({dur/60:.1f}분) → {dl}")
    telegram_send(OUT, f"🎧 위원회 — 중앙·시·도 4층 건물로 다시 정리한 선생님 강의 ({dur/60:.0f}분). committees.html 🏛 탭 + 🎧 탭")


if __name__ == "__main__":
    asyncio.run(main())
