"""플래시카드 문항 검수: 문장 깨짐·정답 오류·해설 모순을 claude(Fable 5.1)가 챕터별로 판정 → audit/qa_<ch>.json + 요약 리포트.
  python3 qa_audit.py ch73            # 한 챕터
  python3 qa_audit.py ch              # 접두사 전체 (ch01~ch82)
  python3 qa_audit.py ch73 --apply    # high 확신 수정안 적용 (백업 audit/bak_<ch>.json)
수정은 --apply 없이는 절대 하지 않음. 결과만 기록."""
import json, re, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(__file__).resolve().parent
CH_DIR = BASE / "public" / "chapters"
OUT_DIR = BASE / "audit"
CLAUDE = "/opt/homebrew/bin/claude"
MODEL = "claude-fable-5-1"
BATCH = 20
WORKERS = 3
TIMEOUT = 900
ENV_FILE = Path.home() / "telegram-claude-bridge" / ".env"

PROMPT = """당신은 한국 행정법·소방법 기출 문제집 검수 전문가입니다. 아래 OX/객관식 문항 목록을 검수하세요.
각 문항에 대해 다음만 확인합니다:
 1. 문장깨짐: q(문제)가 비문·단어 누락·중복으로 뜻이 통하지 않음 (오타 수준은 무시)
 2. 정답오류: ans가 현행 법령·대법원/헌재 판례·통설에 비추어 틀림 (확신할 때만)
 3. 해설모순: exp가 ans와 반대로 설명하거나, 지문과 해설이 서로 같은 말을 하는데 X로 처리된 경우 등
 4. 형식오류: 객관식(①~⑤)인데 선택지가 없음, ans 값이 비정상
문제 없는 문항은 절대 언급하지 마세요. 애매하면 confidence를 "mid"로 두세요.
반드시 아래 JSON만 출력:
{"issues":[{"no":"문항no","type":"문장깨짐|정답오류|해설모순|형식오류","confidence":"high|mid","detail":"한두 문장 근거(조문·판례 포함)","fix":{"ans":"필요시","q":"필요시 고친 문장","exp":"필요시 고친 해설"}}]}
문제가 하나도 없으면 {"issues":[]}

[챕터] {title}
[문항]
{items}"""


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def claude(prompt: str) -> str:
    r = subprocess.run([CLAUDE, "-p", "--model", MODEL, "--output-format", "text", "--tools", ""],
                       input=prompt, capture_output=True, text=True, timeout=TIMEOUT, cwd="/tmp")
    if r.returncode != 0 and not (r.stdout and r.stdout.strip()):
        raise RuntimeError(f"claude 실패: {(r.stderr or r.stdout)[-300:]}")
    return r.stdout


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSON 없음: " + text[:200])
    return json.loads(m.group(0), strict=False)


def fmt(q: dict) -> str:
    return f"[no={q['no']}] ans={q['ans']} src={q.get('src','')}\nQ: {q['q']}\nEXP: {q.get('exp','')}\n"


def audit_batch(title: str, qs: list[dict]) -> list[dict]:
    prompt = PROMPT.replace("{title}", title).replace("{items}", "\n".join(fmt(q) for q in qs))
    for attempt in (1, 2):
        try:
            return parse_json(claude(prompt)).get("issues", [])
        except Exception as e:
            log(f"  배치 실패({attempt}): {str(e)[:120]}")
            time.sleep(10)
    return [{"no": qs[0]["no"], "type": "검수실패", "confidence": "mid", "detail": "claude 응답 파싱 실패 배치", "fix": {}}]


def audit_chapter(ch: str) -> dict:
    data = json.loads((CH_DIR / f"{ch}.json").read_text(encoding="utf-8"))
    qs = data["questions"]
    issues: list[dict] = []
    for i in range(0, len(qs), BATCH):
        issues += audit_batch(data["title"], qs[i:i + BATCH])
    nos = {q["no"] for q in qs}
    issues = [x for x in issues if str(x.get("no")) in nos]
    res = {"ch": ch, "title": data["title"], "total": len(qs), "issues": issues}
    (OUT_DIR / f"qa_{ch}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{ch} 완료: {len(qs)}문항 중 지적 {len(issues)}건 (high {sum(1 for x in issues if x.get('confidence')=='high')})")
    return res


def apply_fixes(ch: str) -> int:
    res = json.loads((OUT_DIR / f"qa_{ch}.json").read_text(encoding="utf-8"))
    path = CH_DIR / f"{ch}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fixes = {str(x["no"]): x["fix"] for x in res["issues"] if x.get("confidence") == "high" and x.get("fix")}
    if not fixes:
        return 0
    shutil.copy(path, OUT_DIR / f"bak_{ch}.json")
    new_qs = [{**q, **{k: v for k, v in fixes.get(str(q["no"]), {}).items() if k in ("ans", "q", "exp") and v}}
              for q in data["questions"]]
    path.write_text(json.dumps({**data, "questions": new_qs}, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(fixes)


def telegram_text(text: str) -> None:
    env = dict(l.split("=", 1) for l in ENV_FILE.read_text().splitlines() if "=" in l and not l.startswith("#"))
    token, chat = env.get("TELEGRAM_BOT_TOKEN", "").strip(), env.get("ALLOWED_USERS", "").split(",")[0].strip()
    if token and chat:
        subprocess.run(["curl", "-s", f"https://api.telegram.org/bot{token}/sendMessage",
                        "-d", f"chat_id={chat}", "--data-urlencode", f"text={text[:3900]}"], capture_output=True, timeout=60)


def report(results: list[dict]) -> str:
    lines = [f"🔍 문항 검수 완료 — {len(results)}챕터 {sum(r['total'] for r in results)}문항"]
    tot_h = sum(1 for r in results for x in r["issues"] if x.get("confidence") == "high")
    tot_m = sum(1 for r in results for x in r["issues"] if x.get("confidence") != "high")
    lines.append(f"지적: high {tot_h}건 / mid {tot_m}건")
    for r in results:
        if r["issues"]:
            h = [x["no"] for x in r["issues"] if x.get("confidence") == "high"]
            m = [x["no"] for x in r["issues"] if x.get("confidence") != "high"]
            lines.append(f"- {r['ch']}: high {','.join(map(str,h)) or '-'} | mid {','.join(map(str,m)) or '-'}")
    lines.append("상세: audit/qa_<ch>.json · 적용은 python3 qa_audit.py <ch> --apply")
    txt = "\n".join(lines)
    (OUT_DIR / "qa_report.md").write_text(txt, encoding="utf-8")
    return txt


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    target = args[0]
    chs = [target] if (CH_DIR / f"{target}.json").exists() else sorted(p.stem for p in CH_DIR.glob(f"{target}*.json"))
    if not chs:
        raise SystemExit(f"챕터 없음: {target}")
    if "--apply" in sys.argv:
        n = sum(apply_fixes(c) for c in chs)
        log(f"적용 완료: {n}문항"); return
    log(f"검수 시작: {len(chs)}챕터 ({chs[0]}~{chs[-1]})")
    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(audit_chapter, chs))
    txt = report(results)
    print(txt)
    telegram_text(txt)


if __name__ == "__main__":
    main()
