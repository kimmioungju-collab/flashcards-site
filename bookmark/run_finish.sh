#!/bin/zsh
# 두 백그라운드 작업(map_exam, odd_scan) 종료 대기 → 오류분 재시도 → 음영 적용 → 텔레그램 전송
cd ~/projects/flashcards-site/bookmark
LOG=../audit/bookmark/finish.log
while pgrep -f "python3 map_exam.py|python3 odd_scan.py" >/dev/null; do sleep 60; done
echo "$(date) 1차 완료, 오류분 재시도" >> $LOG
python3 map_exam.py --workers 6 >> $LOG 2>&1
python3 odd_scan.py --workers 4 >> $LOG 2>&1
echo "$(date) 음영 적용 시작" >> $LOG
python3 apply_marks.py --send >> $LOG 2>&1
python3 - >> $LOG 2>&1 <<'PY'
import json
from common import OUT_DIR, telegram_text, load_jsonl, EXAM_MARKS, ODD_MARKS
r = json.load(open(OUT_DIR / "apply_report.json"))
ex = load_jsonl(EXAM_MARKS); err = sum(1 for x in ex if x.get("error")); noq = sum(1 for x in ex if not x.get("error") and not x["marks"])
lines = ["✅ 소방전술 공통교재 기출 표시 완료 — iCloud 전술/표시/ 폴더"]
for b, v in sorted(r.items()):
    s = v["stats"]
    lines.append(f"📗 {v['file'].split('/')[-1]}: 🟡{s.get('ans',0)} 🔴{s.get('wrong',0)} 🟢{s.get('odd',0)} (미매칭 {len(v['misses'])})")
lines.append(f"문항 {len(ex)}개 처리 / 오류 {err} / 교재 근거 못 찾음 {noq}")
telegram_text("\n".join(lines))
PY
echo "$(date) 끝" >> $LOG
