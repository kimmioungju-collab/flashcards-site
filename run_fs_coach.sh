#!/bin/zsh
# 소방공무원법 fs01~fs07: 핵심요약 원문 교체(fs_summary_html.py, 별도 기동) 완료 대기 → --coach 대본+Airy TTS → 배포 → 커밋 → 텔레그램 알림
set -u
cd "$(dirname "$0")"
CHAPTERS=(fs01 fs02 fs03 fs04 fs05 fs06 fs07)
SUM_DIR=/tmp/fs_sum
LOG=/tmp/fs_coach_run.log
export LECTURE_TTS=edge LECTURE_EDGE_VOICE=ko-KR-SunHiNeural LECTURE_CLAUDE=opus   # 사용자 선택 2026-09-13: MS SunHi(여). Airy 음성은 억양·감탄사 처리 불량

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

notify() {
  local token chat
  token=$(grep '^TELEGRAM_BOT_TOKEN=' ~/telegram-claude-bridge/.env | cut -d= -f2-)
  chat=$(grep '^ALLOWED_USERS=' ~/telegram-claude-bridge/.env | cut -d= -f2- | tr -d '"' | cut -d, -f1)
  curl -s -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" --data-urlencode "text=$1" > /dev/null
}

# 1. 핵심요약 변환 완료 대기 (최대 40분)
log "핵심요약 변환 대기"
for _ in $(seq 1 240); do
  pgrep -f fs_summary_html.py > /dev/null || break
  sleep 10
done
FAILED=()
for ch in $CHAPTERS; do
  [[ -f "$SUM_DIR/$ch.intro.html" ]] || FAILED+=("$ch")
done
if (( ${#FAILED} )); then
  log "핵심요약 변환 실패: ${FAILED[*]}"
  notify "⚠️ 소방공무원법 핵심요약 원문 교체 실패: ${FAILED[*]} — 로그 $SUM_DIR/*.convert.log"
fi

# 2. 챕터별 대본 + TTS (Airy 분당 15회 제한 → 순차)
OK=(); BAD=()
for ch in $CHAPTERS; do
  [[ -f "$SUM_DIR/$ch.intro.html" ]] || { BAD+=("$ch"); continue; }
  log "$ch 대본·TTS 시작"
  if python3 lecture_pod.py "$ch" --coach --reuse >> "/tmp/fs_coach_$ch.log" 2>&1; then
    OK+=("$ch"); log "$ch 완료"
  else
    BAD+=("$ch"); log "$ch 실패 (로그 /tmp/fs_coach_$ch.log)"
  fi
done

# 3. 배포 + 커밋
log "Firebase 배포"
npx firebase-tools deploy --only hosting >> "$LOG" 2>&1 && DEPLOY=OK || DEPLOY=FAIL
git add public/intros/fs0[1-7].json public/audio/nlm/fs0[1-7]_full.* lecture_pod.py fs_summary_html.py run_fs_coach.sh
git commit -q -m "feat: 소방공무원법 fs01~fs07 핵심요약 교재 원문 교체 + 1타강사 암기 대본(--coach) Airy 음성 재생성" && log "커밋 완료" || log "커밋 없음"

# 4. 결과 알림
SUMMARY=""
for ch in $CHAPTERS; do
  mp3="public/audio/nlm/${ch}_full.mp3"
  if [[ -f "$mp3" ]]; then
    dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$mp3" 2>/dev/null | cut -d. -f1)
    SUMMARY+="$ch $((dur/60))분$((dur%60))초"$'\n'
  fi
done
notify "🎧 소방공무원법 음성 재생성 완료 (핵심요약 = 교재 원문, 대본 = 1타강사 암기 톤)
성공: ${OK[*]:-없음}
실패: ${BAD[*]:-없음}
배포: $DEPLOY
$SUMMARY
https://work-schedule-dash-4ceb2.web.app"
log "끝"
