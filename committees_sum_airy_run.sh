#!/bin/zsh
# 위원회 핵심요약 낭독: Airy 합성+검증 → 배포 → 커밋 → 텔레그램 전송
set -e
cd /Users/kmj/projects/flashcards-site
python3 committees_book_audio.py
python3 - <<'PY'
import re
from pathlib import Path
p = Path("public/committees.html"); s = p.read_text(encoding="utf-8")
p.write_text(re.sub(r"BOOK_V=(\d+)", lambda m: f"BOOK_V={int(m.group(1)) + 1}", s, count=1), encoding="utf-8")
PY
npx firebase-tools deploy --only hosting --project work-schedule-dash-4ceb2
git add committees_sum_airy_run.sh public/committees.html public/audio/committees_book.json public/audio/committees_book_*.mp3 public/audio/committees_book_script.txt committees_book_audio.py committees_book_script_body.bak.txt
git commit -m "feat: 위원회 교재 핵심요약 낭독 음성 (Airy) 교체"
python3 committees_book_audio.py --send
