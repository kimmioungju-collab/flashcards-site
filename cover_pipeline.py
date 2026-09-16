#!/usr/bin/env python3
"""챕터별 순차 파이프라인 (사용자 지시 2026-09-14): 요약 커버리지 검증·보강(intro_audit) → 문항→요약 매핑(intro_map) → 배포 → 텔레그램 보고.
한 번에 한 챕터만. 순서: ch53→ch81, 이어서 ch01→ch52.  사용: python3 cover_pipeline.py [시작챕터]
"""
import subprocess
import sys
from datetime import datetime

import intro_audit
import intro_map
from run_intro_audit import ROOT, tg

AUDIT_PASSES = 2          # run() 한 번에 최대 3라운드 보강 → 그래도 남으면 한 번 더
ORDER = [f"ch{i:02d}" for i in range(53, 82)] + [f"ch{i:02d}" for i in range(1, 53)]


def deploy() -> bool:
    r = subprocess.run(["npx", "firebase-tools", "deploy", "--only", "hosting", "--project", "work-schedule-dash-4ceb2"],
                       cwd=ROOT, capture_output=True, text=True, timeout=1800)
    return "Deploy complete" in (r.stdout + r.stderr)


def one(ch: str) -> str:
    first, final = None, None
    for _ in range(AUDIT_PASSES):
        rep = intro_audit.run(ch)
        if first is None:
            first = len(rep["rounds"][0]["missing"])
        final = rep["final_missing"]
        if final == 0:
            break
    m = intro_map.build(ch)
    ok = deploy()
    cover = f"미커버 {first}→{final}" + ("" if final == 0 else " ⚠️ 아직 남음")
    return (f"{'✅' if ok and final == 0 else '⚠️'} {ch} 검증·매핑 {'배포됨' if ok else '배포 실패'} · "
            f"{rep['questions']}문항 {cover} · 매핑 {m['quote'] + m['sec_only']}개(인용 {m['quote']}, 미매핑 {m['none']})")


def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else "ch53"
    chs = ORDER[ORDER.index(start):]
    for ch in chs:
        print(f"=== {ch} {datetime.now():%H:%M} ===", flush=True)
        try:
            msg = one(ch)
        except Exception as e:  # 한 챕터 실패가 전체를 멈추지 않도록
            msg = f"❌ {ch} 실패: {str(e)[:160]}"
        print(msg, flush=True)
        tg(msg)
    tg("🎉 요약 검증·매핑 파이프라인 전 챕터 종료")


if __name__ == "__main__":
    main()
