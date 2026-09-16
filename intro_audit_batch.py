#!/usr/bin/env python3
"""행정법 핵심요약 전수검사 — 4시간마다 5챕터씩만 처리하는 배치 (LaunchAgent com.kmj.intro-audit-4h).
상태 파일(pending 목록)은 재부팅에도 남도록 ~/files 에 둔다. 사용: python3 intro_audit_batch.py [--reset ch41 ch43 ...]
"""
import fcntl
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from run_intro_audit import tg

ROOT = Path(__file__).resolve().parent
STATE = Path.home() / "files" / "intro_audit_state.json"
LOCK = Path.home() / "files" / "intro_audit_batch.lock"
RESULT = Path("/tmp/intro_audit/batch_result.json")
BATCH = 5
MAX_RETRY = 2


def load() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"pending": [], "retries": {}, "done": []}


def save(state: dict) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def run_batch(chs: list[str]) -> list[dict]:
    subprocess.run([sys.executable, str(ROOT / "run_intro_audit.py"), *chs], cwd=ROOT, timeout=3 * 3600)
    return json.loads(RESULT.read_text()) if RESULT.exists() else []


def next_state(state: dict, chs: list[str], results: list[dict]) -> dict:
    """처리된 챕터는 pending에서 제거. 실패한 챕터는 MAX_RETRY까지 뒤로 미룸 (새 객체 반환)."""
    by_ch = {r["ch"]: r for r in results}
    retries = dict(state["retries"])
    failed = []
    for ch in chs:
        if "error" in by_ch.get(ch, {"error": "결과 없음"}) and retries.get(ch, 0) < MAX_RETRY:
            retries[ch] = retries.get(ch, 0) + 1
            failed.append(ch)
    pending = [c for c in state["pending"] if c not in chs] + failed
    done = state["done"] + [c for c in chs if c not in failed]
    return {"pending": pending, "retries": retries, "done": done, "last_run": datetime.now().isoformat(timespec="minutes")}


def main() -> None:
    if "--reset" in sys.argv:
        save({"pending": sys.argv[sys.argv.index("--reset") + 1:], "retries": {}, "done": []})
        return
    with open(LOCK, "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # 이전 배치가 아직 실행 중
        state = load()
        if not state["pending"]:
            return
        chs = state["pending"][:BATCH]
        results = run_batch(chs)
        new = next_state(state, chs, results)
        save(new)
        tg(f"📖 요약검사 배치 {', '.join(chs)} 처리 · 남은 챕터 {len(new['pending'])}개"
           + ("\n🎉 전 챕터 완료" if not new["pending"] else " (다음 배치 4시간 뒤)"))


if __name__ == "__main__":
    main()
