"""위원회 강의 mp3에서 edge 폴백(48kbps)으로 합성된 청크만 다시 합성해 재조립.
  python3 committees_audio_fix.py          # Airy(한도 리셋 후)
  python3 committees_audio_fix.py edge     # edge SunHi(여성)로 임시 교체
"""
import asyncio, json, shutil, subprocess, sys, time
from pathlib import Path
import edge_tts
import lecture_pod as LP
from nlm_audio import telegram_send

BASE = Path(__file__).resolve().parent
TMP = Path("/tmp/committees_audio")
OUT = BASE / "public" / "audio" / "committees_teacher.mp3"
TL = BASE / "public" / "audio" / "committees_teacher.json"
EDGE_VOICE = "ko-KR-SunHiNeural"
AIRY_BITRATE = 64000


def bitrate(p: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=bit_rate", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return int(r.stdout.strip() or 0)


def main() -> None:
    use_edge = "edge" in sys.argv[1:]
    tl = json.loads(TL.read_text(encoding="utf-8"))
    files = [Path(l.split("'")[1]) for l in (TMP / "final.txt").read_text().splitlines()]
    chunks = [f for f in files if f.name.startswith("s")]
    if len(chunks) != len(tl):
        raise SystemExit(f"청크 수 불일치 {len(chunks)} vs {len(tl)}")
    bad = [i for i, f in enumerate(chunks) if bitrate(f) != AIRY_BITRATE]
    LP.log(f"재합성 대상 {len(bad)}개: {[chunks[i].name for i in bad]} ({'edge' if use_edge else 'airy'})")
    if not bad:
        LP.log("재합성할 청크 없음"); return
    fixed = 0
    for i in bad:
        f, txt = chunks[i], tl[i]["t"]
        try:
            if use_edge:
                asyncio.run(edge_tts.Communicate(txt, EDGE_VOICE, rate=LP.RATE).save(str(f)))
            else:
                LP.airy_synth_sync(txt, f); time.sleep(LP.AIRY_RPM_GAP)
            fixed += 1
        except Exception as e:
            LP.log(f"  실패 {f.name}: {str(e)[:120]}")
            if "quota" in str(e):
                break
    if not fixed:
        raise SystemExit("하나도 재합성 못 함 (한도?)")
    t, new = 0.0, []
    k = 0
    for f in files:
        d = LP.duration(f)
        if f.name.startswith("s"):
            new.append({"s": round(t, 2), "e": round(t + d, 2), "t": tl[k]["t"]}); k += 1
        t += d
    final = TMP / "final.mp3"
    LP.concat(files, final); shutil.copy(final, OUT)
    TL.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    shutil.copy(OUT, Path.home() / "Downloads" / "소방공무원법_위원회_선생님강의.mp3")
    LP.log(f"완료: {fixed}/{len(bad)} 교체, {LP.duration(OUT)/60:.1f}분")
    telegram_send(OUT, f"🎧 위원회 강의 — {'Airy 원래 목소리로 끝부분 재합성 완료' if not use_edge else '끝부분 여성 목소리(edge)로 임시 교체'} ({LP.duration(OUT)/60:.0f}분)")


if __name__ == "__main__":
    main()
