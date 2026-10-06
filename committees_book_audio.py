"""교재 본문 위원회 낭독 대본 → 위원회별 mp3 + 화면용 타임라인(public/audio/committees_book.json).
줄을 Airy 한도(640자) 안에서 묶어 Airy TTS(Silvia)로 합성하고 앞뒤 무음을 잘라 낸 뒤, 묶음 사이에 쉼을 넣어 잇는다.
묶음 안의 줄별 시각은 글자 수 비례로 나눈다. Airy 게스트 하루 한도(30요청)에 걸리면 거기서 멈추고,
다음 날 다시 실행하면 캐시된 묶음은 건너뛰고 남은 트랙만 만든다.

  python3 committees_book_audio.py                 # 전체 합성 + Whisper 검증 (백그라운드 권장)
  python3 committees_book_audio.py 05 08           # 일부 트랙만
  python3 committees_book_audio.py --no-verify     # 검증 생략
  python3 committees_book_audio.py --verify-only 01 02   # 합성 없이 해당 트랙만 검증
  python3 committees_book_audio.py --send          # 합성 없이, 만들어 둔 mp3를 텔레그램으로 전송
"""
import asyncio
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import committees_book as book
import lecture_pod as LP
from nlm_audio import telegram_send

BASE = Path(__file__).resolve().parent
AUDIO_DIR = BASE / "public" / "audio"
MANIFEST = AUDIO_DIR / "committees_book.json"
TMP = Path("/tmp/committees_book_audio")
CLIPS = TMP / "clips"
DOWNLOADS = Path.home() / "Downloads" / "소방공무원법_위원회_교재원문"
VOICE = f"airy:{LP.AIRY_VOICE}"       # Airy Silvia — 강의 파이프라인과 같은 목소리 (사용자 지시 2026-10-06)
CHUNK_MAX = 600                       # Airy 640자 한도 아래로 묶음 (요청 수 = 하루 한도 30회 기준 이틀치)
CHUNK_SPLIT_AT = 250                  # 이만큼 찼으면 큰 제목(h1·h2) 앞에서 묶음을 끊는다
RETRY = 2
MIN_SEC_PER_CHAR = 0.07               # 이보다 짧으면 합성 누락으로 본다
SAMPLE_RATE = 24000
TRIM = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.03"
GAP_AFTER = {"t": 0.9, "h1": 0.7, "h2": 0.6, "h3": 0.5, "p": 0.5, "n1": 0.5, "box": 0.5, "n2": 0.35, "n3": 0.3}
GAP_BEFORE = {"h1": 1.0, "h2": 0.9, "h3": 0.75}      # 제목 앞은 더 길게 쉰다
QUOTA_CODE = "guest_daily_speech_quota_exceeded"


class QuotaExceeded(RuntimeError):
    """Airy 하루 한도 소진 — 오늘은 더 못 만든다."""
WHISPER_MODEL = "small"
VERIFY_MIN = 0.8                      # Whisper 받아쓰기와 낭독문 일치율 하한


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"명령 실패: {' '.join(cmd[:4])} … {r.stderr.strip()[-300:]}")


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError as e:
        raise RuntimeError(f"길이 확인 실패: {path}") from e


def pack(items: list[dict]) -> list[list[dict]]:
    """줄들을 CHUNK_MAX 자 안에서 묶는다. 트랙 경계는 넘지 않고, 어느 정도 찼으면 큰 제목 앞에서 끊는다."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for it in items:
        used = sum(len(i["sp"]) + 1 for i in cur)
        if cur and (used + len(it["sp"]) > CHUNK_MAX or (it["k"] in ("h1", "h2") and used > CHUNK_SPLIT_AT)):
            chunks.append(cur)
            cur = []
        cur = [*cur, it]
    return [*chunks, cur] if cur else chunks


async def clip(text: str) -> Path:
    """묶음 하나 → 앞뒤 무음을 잘라 낸 wav (같은 글은 캐시 재사용 — 한도에 걸려 다음 날 이어 만들 때 요청을 아낀다)."""
    out = CLIPS / (hashlib.sha1(f"{VOICE}|{text}".encode()).hexdigest() + ".wav")
    if out.exists():
        return out
    raw = out.with_suffix(".mp3")
    for attempt in range(1, RETRY + 1):
        try:
            await LP.airy_synth(text, raw)
            await asyncio.to_thread(run, ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
                                          f"{TRIM},areverse,{TRIM},areverse", "-ar", str(SAMPLE_RATE), "-ac", "1", str(out)])
            if duration(out) >= len(text) * MIN_SEC_PER_CHAR:
                raw.unlink(missing_ok=True)
                return out
            log(f"  길이 의심({duration(out):.1f}s/{len(text)}자) 재시도 {attempt}: {text[:30]}")
        except Exception as e:  # 네트워크 오류 등은 다시 시도, 한도 소진은 바로 멈춤
            if QUOTA_CODE in str(e):
                raise QuotaExceeded("Airy 하루 한도(30요청) 소진") from e
            log(f"  합성 오류 {attempt}: {str(e)[:120]}")
        out.unlink(missing_ok=True)
        await asyncio.sleep(3 * attempt)
    raise RuntimeError(f"합성 {RETRY}회 실패: {text[:40]}")


def silence(sec: float) -> Path:
    out = TMP / f"gap_{int(sec * 1000)}.wav"
    if not out.exists():
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
             "-t", str(sec), str(out)])
    return out


def split_timeline(chunk: list[dict], start: float, dur: float) -> list[dict]:
    """묶음 길이를 줄별 글자 수에 비례해 나눠 화면용 시각을 만든다."""
    total = sum(len(i["sp"]) for i in chunk) or 1
    tl, t = [], start
    for it in chunk:
        d = dur * len(it["sp"]) / total
        tl = [*tl, {"s": round(t, 2), "e": round(t + d, 2), "k": it["k"], "t": it["d"]}]
        t += d
    return tl


async def build_track(tr: dict) -> dict:
    """트랙 하나 합성 → mp3 저장, 타임라인이 담긴 목록 항목 반환."""
    chunks = pack(tr["items"])
    wavs = [await clip("\n".join(i["sp"] for i in c)) for c in chunks]     # 한도(RPM) 때문에 순차
    files, tl, t = [], [], 0.0
    for n, (chunk, wav) in enumerate(zip(chunks, wavs)):
        d = duration(wav)
        files = [*files, wav]
        tl = [*tl, *split_timeline(chunk, t, d)]
        t += d
        if n < len(chunks) - 1:
            gap = max(GAP_AFTER[chunk[-1]["k"]], GAP_BEFORE.get(chunks[n + 1][0]["k"], 0.0))
            files = [*files, silence(gap)]
            t += gap
    lst = TMP / f"track_{tr['no']}.txt"
    lst.write_text("".join(f"file '{f}'\n" for f in files), encoding="utf-8")
    out = AUDIO_DIR / f"committees_book_{tr['no']}.mp3"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-metadata", f"title={tr['no']} {tr['title']} ({tr['ch']})", "-metadata", "artist=소방공무원법 교재 원문",
         "-metadata", "album=위원회 교재 원문 낭독", "-metadata", f"track={int(tr['no'])}",
         "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", str(out)])
    dur = duration(out)
    log(f"  {tr['no']} {tr['title']}: {len(tr['items'])}줄/{len(chunks)}묶음 → {dur / 60:.1f}분")
    return {"no": tr["no"], "ch": tr["ch"], "title": tr["title"], "pages": tr["pages"], "dur": round(dur, 1),
            "voice": VOICE, "tl": tl}


def hangul(t: str) -> str:
    return re.sub(r"[^가-힣]", "", t)


def verify_track(tr: dict) -> float:
    """Whisper로 받아쓴 글과 낭독문의 한글 일치율."""
    mp3 = AUDIO_DIR / f"committees_book_{tr['no']}.mp3"
    out_dir = TMP / "stt"
    run(["whisper", str(mp3), "--model", WHISPER_MODEL, "--language", "ko", "--output_format", "txt",
         "--output_dir", str(out_dir), "--fp16", "False", "--verbose", "False"])
    heard = hangul((out_dir / f"{mp3.stem}.txt").read_text(encoding="utf-8"))
    said = hangul(" ".join(i["sp"] for i in tr["items"]))
    return difflib.SequenceMatcher(None, said, heard, autojunk=False).ratio()


def save_manifest(entries: list[dict]) -> None:
    """이번에 만든 트랙을 기존 목록에 덮어써 저장 (일부 트랙만 다시 만들 때 나머지 보존)."""
    old = json.loads(MANIFEST.read_text(encoding="utf-8"))["tracks"] if MANIFEST.exists() else []
    merged = {e["no"]: e for e in old} | {e["no"]: e for e in entries}
    tracks = [merged[k] for k in sorted(merged)]
    MANIFEST.write_text(json.dumps({"voice": VOICE, "tracks": tracks}, ensure_ascii=False), encoding="utf-8")


def export_downloads(entries: list[dict]) -> list[Path]:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    outs = []
    for e in entries:
        dst = DOWNLOADS / f"{e['no']} {e['title']} ({e['ch']}).mp3"
        shutil.copy(AUDIO_DIR / f"committees_book_{e['no']}.mp3", dst)
        outs.append(dst)
    return outs


def send(only: list[str]) -> None:
    """만들어 둔 mp3 전송. 번호를 주면 그 트랙만, 없으면 Airy로 만든 트랙만."""
    entries = [e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))["tracks"]
               if (e["no"] in only if only else e.get("voice") == VOICE)]
    for e, path in zip(entries, export_downloads(entries)):
        telegram_send(path, f"🎧 {e['no']}/{len(entries):02d} {e['title']} — {e['ch']} · 교재 {e['pages']} ({e['dur'] / 60:.0f}분)")
        time.sleep(1.5)


async def synth(only: list[str]) -> list[dict]:
    """트랙을 차례로 만든다. 한도에 걸리면 그때까지 완성된 트랙만 저장하고 남은 트랙 번호를 알린다."""
    tracks = [t for t in book.parse() if not only or t["no"] in only]
    if not tracks:
        raise SystemExit(f"해당 트랙 없음: {only}")
    CLIPS.mkdir(parents=True, exist_ok=True)
    log(f"트랙 {len(tracks)}개 / {sum(len(t['items']) for t in tracks)}줄 / "
        f"{sum(len(pack(t['items'])) for t in tracks)}묶음 / {sum(len(i['sp']) for t in tracks for i in t['items'])}자")
    done: list[dict] = []
    entries: list[dict] = []
    for tr in tracks:
        try:
            entries = [*entries, await build_track(tr)]
            done = [*done, tr]
        except QuotaExceeded as e:
            left = [t["no"] for t in tracks if t not in done]
            log(f"{e} → 완성 {[t['no'] for t in done]}, 남은 트랙 {left} (내일 다시 실행하면 이어서 만듦)")
            break
    if entries:
        save_manifest(entries)
    log(f"합성 완료: {len(entries)}트랙, 총 {sum(e['dur'] for e in entries) / 60:.1f}분")
    return done


def main() -> None:
    args = sys.argv[1:]
    only = [a for a in args if a.isdigit()]
    if "--send" in args:
        send(only)
        return
    if "--verify-only" in args:               # 이미 만든 mp3만 검증 (트랙을 나눠 돌릴 때)
        tracks = [t for t in book.parse() if not only or t["no"] in only]
    else:
        tracks = asyncio.run(synth(only))
    if "--no-verify" in args:
        return
    bad = []
    for tr in tracks:
        score = verify_track(tr)
        log(f"  검증 {tr['no']} {tr['title']}: 일치율 {score:.3f}")
        if score < VERIFY_MIN:
            bad.append(tr["no"])
    if bad:
        raise SystemExit(f"검증 미달 트랙: {bad} (기준 {VERIFY_MIN})")
    log("검증 통과: 전 트랙")


if __name__ == "__main__":
    main()
