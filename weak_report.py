#!/usr/bin/env python3
"""챕터별 오답·취약 문제 추출 스크립트.

사용법: python3 weak_report.py ch05
출력: 해당 챕터의 오답(누적 틀림) + 취약(⭐체크) 문제 목록 (문제/정답/해설 포함)

데이터 출처:
  - 문제: https://work-schedule-dash-4ceb2.web.app/chapters/chNN.json
  - 학습기록: Firebase RTDB flashcards/kmj/ch/chNN (qstat: {s:본횟수, w:틀린횟수, m:체크})
"""
import json
import sys
import urllib.request

SITE = "https://work-schedule-dash-4ceb2.web.app"
DB = "https://work-schedule-dash-4ceb2-default-rtdb.firebaseio.com"
SYNC = "1111"  # 사용자 실제 동기화 코드 (kmj 아님! 2026-08-04 확인 — 82챕터 기록 보유)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    if len(sys.argv) < 2:
        print("사용법: python3 weak_report.py ch05")
        sys.exit(1)

    ch = sys.argv[1].strip().lower()
    if not ch.startswith("ch"):
        ch = "ch" + ch.zfill(2)
    if len(ch) == 3:  # ch5 -> ch05
        ch = "ch" + ch[2:].zfill(2)

    try:
        chap = fetch_json(f"{SITE}/chapters/{ch}.json")
    except Exception as e:
        print(f"ERROR: 챕터 파일({ch}.json) 로드 실패 — {e}")
        sys.exit(2)

    try:
        stat = fetch_json(f"{DB}/flashcards/{SYNC}/ch/{ch}.json") or {}
    except Exception:
        stat = {}

    qstat = stat.get("qstat") or {}
    last_wrong = set(stat.get("wrongNos") or [])

    weak = []
    for q in chap.get("questions", []):
        no = str(q.get("no", ""))
        st = qstat.get(no) or {}
        w = st.get("w", 0) or 0
        m = bool(st.get("m"))
        in_last = no in last_wrong
        if w > 0 or m or in_last:
            weak.append({
                "no": no,
                "grade": q.get("grade", ""),
                "theme": q.get("theme", ""),
                "q": q.get("q", ""),
                "ans": q.get("ans", ""),
                "exp": q.get("exp", ""),
                "wrong_count": w,
                "marked": m,
                "last_round_wrong": in_last,
            })

    # 오답 많은 순 → 체크 순
    weak.sort(key=lambda x: (x["wrong_count"], x["marked"]), reverse=True)

    out = {
        "chapter": ch,
        "title": chap.get("title", ""),
        "total_questions": len(chap.get("questions", [])),
        "rounds": stat.get("rounds", 0),
        "weak_count": len(weak),
        "weak": weak,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
