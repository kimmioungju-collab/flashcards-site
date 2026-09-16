#!/usr/bin/env python3
"""생성·배포된 팟캐스트(public/audio/nlm/<key>_full.mp3)를 앱 재생 목록(RTDB flashcards/audio/<key>_full)에 등록.
사용: python3 register_audio.py ch52 ts13 ...   (배포가 끝난 뒤 실행할 것)
"""
import json
import sys

from nlm_watch import AUDIO_OUT, BASE, SITE, mp3_minutes, set_state


def register(ch: str) -> str:
    key = f"{ch}_full"
    mp3 = AUDIO_OUT / f"{key}.mp3"
    if not mp3.exists():
        raise FileNotFoundError(f"{mp3} 없음")
    title = json.loads((BASE / "public" / "chapters" / f"{ch}.json").read_text())["title"]
    mins = mp3_minutes(mp3)
    set_state(key, ch=ch, mode="full", status="done", msg="", title=title,
              url=f"{SITE}/audio/nlm/{key}.mp3", mins=mins, size=mp3.stat().st_size)
    return f"{key} {mins}분 등록"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용: register_audio.py ch52 ts13 ...")
    for c in sys.argv[1:]:
        print(register(c), flush=True)
