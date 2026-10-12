#!/bin/zsh
# 남은 단계 순서대로: 화재1 특이스캔 → 화재2 특이스캔 → 구조(기출+특이) → 구급(기출+특이)
# 각 권 끝날 때마다 음영 적용 + 텔레그램 전송. 세션 한도 걸리면 20분 쉬고 재시도.
cd ~/projects/flashcards-site/bookmark
LOG=../audit/bookmark/run_all.log
exec >> $LOG 2>&1

limit_hit() { grep -q "session limit\|usage limit" "$1"; }

run_until_clean() {   # $1=설명, 나머지=명령. 오류분 0이 될 때까지 반복
  local desc=$1; shift
  for try in {1..30}; do
    echo "$(date '+%m/%d %H:%M') [$desc] 시도 $try: $*"
    "$@" > /tmp/bm_stage.log 2>&1
    local n_err=$(grep -c "limit\|JSON 없음\|claude 실패" /tmp/bm_stage.log)
    local n_all=$(grep -c "marks\|odd" /tmp/bm_stage.log)
    echo "  처리 $n_all / 오류 $n_err"
    if limit_hit /tmp/bm_stage.log; then echo "  한도 → 20분 대기"; sleep 1200; continue; fi
    [[ $n_err -eq 0 ]] && return 0
    sleep 60
  done
}

send_book() {   # $1=book
  echo "$(date '+%m/%d %H:%M') 음영 적용·전송 book $1"
  python3 apply_marks.py --books $1 --send
}

run_until_clean "화재1 특이" python3 odd_scan.py --books 10 --workers 4
send_book 10
run_until_clean "화재2 특이" python3 odd_scan.py --books 11 --workers 4
send_book 11
run_until_clean "구조 기출" python3 map_exam.py ts14 ts15 ts16 ts17 ts18 ts19 ts20 --workers 6
run_until_clean "구조 특이" python3 odd_scan.py --books 12 --workers 4
send_book 12
run_until_clean "구급 기출" python3 map_exam.py ts21 ts22 ts23 ts24 ts25 ts26 ts27 ts28 --workers 6
run_until_clean "구급 특이" python3 odd_scan.py --books 13 --workers 4
send_book 13
python3 - <<'PY'
from common import telegram_text
telegram_text("✅ 전술 공통교재 기출 표시 4권 전부 완료 — iCloud 전술/표시/ 폴더 (화재1·화재2·구조·구급_기출표시.pdf)")
PY
echo "$(date '+%m/%d %H:%M') 전부 끝"
