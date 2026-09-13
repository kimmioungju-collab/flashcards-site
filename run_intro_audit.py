#!/usr/bin/env python3
"""행정법 전 챕터 핵심요약 전수 검사·보강 배치 러너.
사용: python3 run_intro_audit.py [ch01 ch02 ...]   (인자 없으면 ch01~ch81 전부, ch52 제외 가능)
동시 3챕터 처리, 10챕터마다 텔레그램 진행 보고, 끝나면 gen_audio + 배포 + 최종 보고.
"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import intro_audit

ROOT = Path(__file__).resolve().parent
ENV_FILE = Path.home() / "telegram-claude-bridge" / ".env"
WORKERS = 3
REPORT_EVERY = 10


def tg(text: str) -> None:
    env = dict(l.split("=", 1) for l in ENV_FILE.read_text().splitlines() if "=" in l and not l.startswith("#"))
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = env.get("ALLOWED_USERS", "").split(",")[0].strip()
    subprocess.run(["curl", "-s", f"https://api.telegram.org/bot{token}/sendMessage",
                    "-d", f"chat_id={chat}", "--data-urlencode", f"text={text}"], capture_output=True)


def one(ch: str) -> dict:
    try:
        return intro_audit.run(ch)
    except Exception as e:  # 한 챕터 실패가 전체를 멈추지 않도록
        return {"ch": ch, "error": str(e)[:200]}


def fmt(r: dict) -> str:
    if "error" in r:
        return f"{r['ch']} ❌ {r['error'][:60]}"
    first = len(r["rounds"][0]["missing"])
    return f"{r['ch']} {r['questions']}문항 · 미커버 {first}→{r['final_missing']}"


def main() -> None:
    chs = sys.argv[1:] or [f"ch{i:02d}" for i in range(1, 82) if i != 52]
    results = []
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(one, ch): ch for ch in chs}
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            print(fmt(r), flush=True)
            if len(results) % REPORT_EVERY == 0:
                done = sorted(results, key=lambda x: x["ch"])[-REPORT_EVERY:]
                tg(f"📖 행정법 요약 전수검사 진행 {len(results)}/{len(chs)}\n" + "\n".join(fmt(x) for x in done))

    results.sort(key=lambda x: x["ch"])
    (Path("/tmp/intro_audit") / "batch_result.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    changed = [r["ch"] for r in results if "error" not in r and len(r["rounds"]) > 1]
    for ch in changed:
        subprocess.run([sys.executable, str(ROOT / "gen_audio.py"), ch], capture_output=True, timeout=3600)
    subprocess.run(["npx", "firebase-tools", "deploy", "--only", "hosting", "--project", "work-schedule-dash-4ceb2"],
                   cwd=ROOT, capture_output=True, timeout=900)
    errs = [r["ch"] for r in results if "error" in r]
    remain = [r["ch"] for r in results if "error" not in r and r["final_missing"]]
    total_first = sum(len(r["rounds"][0]["missing"]) for r in results if "error" not in r)
    tg(f"✅ 행정법 요약 전수검사 완료 {len(results)}챕터\n"
       f"보강 {len(changed)}챕터 · 최초 미커버 {total_first}문항\n"
       f"잔여 미커버: {', '.join(remain) or '없음'}\n실패: {', '.join(errs) or '없음'}\n배포 완료")


if __name__ == "__main__":
    main()
