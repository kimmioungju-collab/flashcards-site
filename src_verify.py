"""교재 OCR 원문 전수 대조: 앱 문항(q·src)이 교재 원문과 다른 것을 찾아 원문으로 복원.
  python3 src_verify.py scan              # 기계 대조만 (audit/srcverify/flagged.json)
  python3 src_verify.py judge [N]         # 의심 문항을 claude가 원문 복원·판정 (N=배치 수 제한, 테스트용)
  python3 src_verify.py apply [--deploy]  # 기계 검증 통과분만 적용 (+커밋·배포·텔레그램)
복원 지문은 OCR 원문과 글자 대조로 재검증해 통과한 것만 반영한다(지어내기 방지)."""
import difflib, json, re, shutil, subprocess, sys, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(__file__).resolve().parent
CH_DIR = BASE / "public" / "chapters"
OUT = BASE / "audit" / "srcverify"
OCR_TXT = Path("/tmp/admin_ocr.txt")
ENV_FILE = Path.home() / "telegram-claude-bridge" / ".env"
CLAUDE = "/opt/homebrew/bin/claude"
MODEL = "claude-fable-5-1"
FIREBASE_PROJECT = "work-schedule-dash-4ceb2"
SKIP_CH = {"ch82"}          # 교재 밖 기출 회차
GRAM, BUCKET = 8, 60
COV_OK, COV_ANCHOR = 0.9, 0.9
COV_ACCEPT, COV_GAIN = 0.85, 0.002  # 복원 지문 채택 기준: 원문 일치 85% 이상 + 기존보다 원문에 더 가까울 것
BATCH, WORKERS, TIMEOUT, RETRY = 10, 3, 900, 2
CTX_BEFORE, CTX_AFTER, SEG_MAX = 350, 300, 2200
VALID_ANS = {"O", "X", "①", "②", "③", "④", "⑤"}
OK = re.compile(r"[0-9A-Za-z가-힣]")

PROMPT = """당신은 교재 원문 대조 검수자입니다. 각 항목은 (A) 플래시카드 앱에 등록된 문항과 (B) 교재(행정법 기출 OX 문제집) OCR 원문 구간입니다.
OCR은 2단 편집이라 다른 문항·판서요약 조각·옆 번호 표식(예: '기 059')이 중간에 끼어들고, 글자 오인식(지→자, 기→가 등)이 있습니다.
각 항목마다:
1. (B)에서 [no] 번호 문항의 원문 지문을 찾아 복원합니다. OCR 오인식만 바로잡고 표현은 원문 그대로 둡니다. 문항 끝의 출처 태그(예: 17국가7, 미기출)는 src로 분리합니다.
2. (A)의 q를 원문과 비교해 판정합니다.
 - same: 띄어쓰기·문장부호·괄호·OCR 오타 수준 차이뿐
 - diff: 단어·구절이 바뀌거나 빠지거나 덧붙어 원문과 다름 (뜻이 비슷해 보여도 원문과 다르면 diff)
 - notfound: (B)에서 해당 문항 원문을 찾을 수 없거나 조각나 복원 불가
3. diff일 때만 orig_q(복원 원문 전문, 객관식은 선택지 포함)를 쓰고, 원문 지문 기준 정답 ans(O/X/①~⑤)와 해설 exp(조문·판례 근거 2~3문장, X면 "틀렸다."로 시작)를 씁니다. 기존 ans·exp가 원문 지문에도 그대로 맞으면 ans·exp는 빈 문자열로 둡니다.
4. 지문은 same인데 출처만 다르면 verdict는 same으로 두고 orig_src만 적습니다. 출처가 같으면 orig_src는 빈 문자열.
확실하지 않으면 notfound. 원문에 없는 문장을 절대 지어내지 마세요.
반드시 아래 JSON만 출력:
{"items":[{"id":"ch73:54","verdict":"same|diff|notfound","orig_q":"","orig_src":"","ans":"","exp":"","note":"한 줄 근거"}]}

{items}"""


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def norm(s: str) -> str:
    return "".join(c for c in s if OK.match(c))


class Ocr:
    """정규화한 OCR 본문과 8글자 조각 색인."""

    def __init__(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(f"OCR 텍스트 없음: {path}")
        self.raw = path.read_text(encoding="utf-8")
        self.pos = [i for i, c in enumerate(self.raw) if OK.match(c)]
        self.n = "".join(self.raw[i] for i in self.pos)
        self.grams = defaultdict(list)
        for i in range(len(self.n) - GRAM + 1):
            self.grams[self.n[i:i + GRAM]].append(i)

    def locate(self, s: str):
        votes, total = Counter(), 0
        for i in range(0, max(1, len(s) - GRAM + 1), 2):
            total += 1
            for k in self.grams.get(s[i:i + GRAM], [])[:8]:
                votes[(k - i) // BUCKET] += 1
        if not votes:
            return None
        return max(0, votes.most_common(1)[0][0] * BUCKET - BUCKET)

    def cover(self, s: str):
        """(일치율, 원문 시작, 원문 끝) — 정규화 좌표."""
        start = self.locate(s)
        if start is None or not s:
            return 0.0, None, None
        win = self.n[start:start + int(len(s) * 1.3) + 200]
        blocks = [m for m in difflib.SequenceMatcher(None, s, win, autojunk=False).get_matching_blocks() if m.size >= 2]
        if not blocks:
            return 0.0, None, None
        cov = sum(m.size for m in blocks) / len(s)
        return cov, start + blocks[0].b, start + blocks[-1].b + blocks[-1].size

    def app_side_diffs(self, s: str, first: int, end: int) -> list:
        ops = difflib.SequenceMatcher(None, s, self.n[first:end], autojunk=False).get_opcodes()
        return [(s[a0:a1], self.n[b0:b1][:30]) for tag, a0, a1, b0, b1 in ops if tag != "equal" and a1 - a0 >= 2]

    def raw_slice(self, a: int, b: int) -> str:
        a, b = max(0, a), min(len(self.pos) - 1, b)
        return re.sub(r"\s+", " ", self.raw[self.pos[a]:self.pos[b]])


def load_chapters() -> dict:
    files = sorted(p for p in CH_DIR.glob("ch*.json") if p.stem not in SKIP_CH)
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


def scan_chapter(ocr: Ocr, ch: str, qs: list) -> list:
    recs = []
    for q in qs:
        cov, first, end = ocr.cover(norm(q["q"]))
        recs.append({"ch": ch, "no": q["no"], "cov": round(cov, 3), "first": first, "end": end})
    flagged = []
    for i, (r, q) in enumerate(zip(recs, qs)):
        src_n = norm(q.get("src", ""))
        tail = ocr.n[r["end"]:r["end"] + len(src_n) + 25] if r["end"] else ""
        reasons = []
        if r["cov"] < COV_OK:
            reasons.append("lowcov")
        elif ocr.app_side_diffs(norm(q["q"]), r["first"], r["end"]):
            reasons.append("diff")
        if "미기출" in q.get("src", "") and "미기출" not in tail:
            reasons.append("src")
        if not reasons:
            continue
        flagged.append({**r, "reasons": reasons, "ctx": context(ocr, recs, i)})
    return flagged


def context(ocr: Ocr, recs: list, i: int) -> str:
    r = recs[i]
    if r["cov"] < COV_ANCHOR:
        prev = next((x for x in reversed(recs[:i]) if x["cov"] >= COV_ANCHOR), None)
        nxt = next((x for x in recs[i + 1:] if x["cov"] >= COV_ANCHOR), None)
        if prev and nxt and 0 < nxt["first"] - prev["end"] < SEG_MAX * 2:
            return ocr.raw_slice(prev["end"] - 60, nxt["first"] + 60)[:SEG_MAX * 2]
    if r["first"] is None:
        return ""
    return ocr.raw_slice(r["first"] - CTX_BEFORE, r["end"] + CTX_AFTER)[:SEG_MAX]


def cmd_scan() -> list:
    ocr, chapters = Ocr(OCR_TXT), load_chapters()
    flagged = [f for ch, d in chapters.items() for f in scan_chapter(ocr, ch, d.get("questions", []))]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "flagged.json").write_text(json.dumps(flagged, ensure_ascii=False), encoding="utf-8")
    total = sum(len(d.get("questions", [])) for d in chapters.values())
    log(f"전체 {total}문항 중 의심 {len(flagged)}건 " + str(dict(Counter(x for f in flagged for x in f["reasons"]))))
    return flagged


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


def fmt_item(f: dict, q: dict) -> str:
    return (f"### id={f['ch']}:{f['no']}  [no] {f['no']}\n(A) ans={q['ans']} src={q.get('src', '')}\n"
            f"q: {q['q']}\nexp: {q.get('exp', '')}\n(B) {f['ctx']}\n")


def judge_batch(idx: int, batch: list, chapters: dict) -> None:
    out = OUT / f"batch_{idx:03d}.json"
    if out.exists():
        return
    qmap = lambda f: next(q for q in chapters[f["ch"]]["questions"] if q["no"] == f["no"])
    prompt = PROMPT.replace("{items}", "\n".join(fmt_item(f, qmap(f)) for f in batch))
    for attempt in range(RETRY + 1):
        try:
            items = parse_json(claude(prompt)).get("items", [])
            out.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
            log(f"배치 {idx} 완료: diff {sum(i.get('verdict') == 'diff' for i in items)}/{len(items)}")
            return
        except Exception as e:  # 재시도 후에도 실패하면 기록만 남기고 다음 배치 진행
            log(f"배치 {idx} 실패({attempt + 1}): {str(e)[:150]}")
    (OUT / f"failed_{idx:03d}.txt").write_text(",".join(f"{f['ch']}:{f['no']}" for f in batch), encoding="utf-8")


def cmd_judge(limit: int = 0) -> None:
    flagged = json.loads((OUT / "flagged.json").read_text(encoding="utf-8"))
    chapters = load_chapters()
    batches = [flagged[i:i + BATCH] for i in range(0, len(flagged), BATCH)]
    if limit:
        batches = batches[:limit]
    log(f"판정 시작: {len(batches)}배치")
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda t: judge_batch(t[0], t[1], chapters), enumerate(batches)))


def decide(ocr: Ocr, q: dict, it: dict, book_ans: str) -> dict:
    """판정 1건을 기계 검증해 반영할 필드만 돌려준다. 통과 못 하면 manual.
    정답(ans)은 절대 바꾸지 않는다 — 교재 정답줄(book_key.json)이 기준이다."""
    new = {}
    src = (it.get("orig_src") or "").strip()
    if src and norm(src) != norm(q.get("src", "")) and norm(src) in ocr.n:
        new["src"] = src
    if it.get("verdict") != "diff":
        return {"new": new, "manual": "원문 못 찾음" if it.get("verdict") == "notfound" else ""}
    if book_ans not in VALID_ANS:
        return {"new": new, "manual": "교재 정답줄에 O/X·번호 정답이 없는 문항(빈칸형·변형 추가문항) — 지문 유지"}
    ai_ans = (it.get("ans") or "").strip()
    if ai_ans and ai_ans != book_ans:
        return {"new": new, "manual": f"복원 지문에 대한 AI 판단({ai_ans})이 교재 정답({book_ans})과 달라 지문 대응 재확인 필요"}
    oq = (it.get("orig_q") or "").strip()
    cov_new, cov_old = ocr.cover(norm(oq))[0], ocr.cover(norm(q["q"]))[0]
    if not oq or cov_new < COV_ACCEPT or cov_new < cov_old + COV_GAIN:
        return {"new": new, "manual": f"복원 지문 검증 미달(원문 일치 {cov_new:.2f}, 기존 {cov_old:.2f})"}
    new["q"] = oq
    if (it.get("exp") or "").strip():
        new["exp"] = it["exp"].strip()
    return {"new": new, "manual": ""}


def patched(q: dict, new: dict) -> dict:
    out = {**q, **new}
    for k, hay in (("kw", out["q"] + out.get("exp", "")), ("kwr", out["q"]), ("kw2", out.get("exp", ""))):
        if k in out and isinstance(out[k], str) and out[k] not in hay:
            out = {x: v for x, v in out.items() if x != k}
    return out


def cmd_apply(deploy: bool) -> None:
    ocr, chapters = Ocr(OCR_TXT), load_chapters()
    book = json.loads((OUT / "book_key.json").read_text(encoding="utf-8"))
    check_answers(chapters, book)
    items = [i for p in sorted(OUT.glob("batch_*.json")) for i in json.loads(p.read_text(encoding="utf-8"))]
    report = {"changed": [], "manual": [], "failed": [p.read_text() for p in sorted(OUT.glob("failed_*.txt"))]}
    updates = defaultdict(dict)
    for it in items:
        ch, _, no = str(it.get("id", "")).partition(":")
        q = next((x for x in chapters.get(ch, {}).get("questions", []) if x["no"] == no), None)
        if q is None:
            continue
        d = decide(ocr, q, it, book.get(it["id"], ""))
        if d["manual"]:
            report["manual"].append({"id": it["id"], "why": d["manual"], "note": it.get("note", "")})
        if d["new"]:
            updates[ch][no] = d["new"]
            report["changed"].append({"id": it["id"], "fields": sorted(d["new"]), "old_q": q["q"], "ans": q["ans"],
                                      "old_src": q.get("src", ""), **d["new"]})
    for ch, upd in updates.items():
        shutil.copy(CH_DIR / f"{ch}.json", OUT / f"bak_{ch}.json")
        data = chapters[ch]
        qs = [patched(q, upd[q["no"]]) if q["no"] in upd else q for q in data["questions"]]
        write_like_original(CH_DIR / f"{ch}.json", {**data, "questions": qs})
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = summarize(report, len(items))
    log(summary)
    if deploy:
        publish(summary, bool(updates))


def write_like_original(path: Path, data: dict) -> None:
    """기존 파일의 들여쓰기·끝줄 서식을 그대로 유지해 저장한다(불필요한 전체 재서식 방지)."""
    raw = path.read_text(encoding="utf-8")
    m = re.search(r"\n( +)\"", raw)
    text = json.dumps(data, ensure_ascii=False, indent=len(m.group(1)) if m else None)
    path.write_text(text + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")


def check_answers(chapters: dict, book: dict) -> None:
    """앱 정답이 교재 정답줄과 다르면 즉시 중단한다(빈칸형 등 O/X·번호가 아닌 키는 제외)."""
    bad = [f"{ch}:{q['no']} 앱={q['ans']} 교재={book[f'{ch}:{q['no']}']}" for ch, d in chapters.items()
           for q in d["questions"] if book.get(f"{ch}:{q['no']}") in VALID_ANS and book[f"{ch}:{q['no']}"] != q["ans"]]
    if bad:
        raise SystemExit("교재 정답줄과 다른 정답 발견 — 중단: " + ", ".join(bad[:20]))


def summarize(report: dict, judged: int) -> str:
    ch = report["changed"]
    q_fix = [c["id"] for c in ch if "q" in c["fields"]]
    src_only = [c["id"] for c in ch if c["fields"] == ["src"]]
    lines = [f"📋 교재 원문 전수 대조 완료 (의심 {judged}건 판정)",
             "• 정답: 교재 왼쪽 하단 정답줄과 전 문항 대조 — 불일치 0건 (정답은 하나도 바꾸지 않음)",
             f"• 지문 원문 복원: {len(q_fix)}건", f"• 출처만 교정: {len(src_only)}건",
             f"• 자동 반영 못 한 것(수동 확인 필요): {len(report['manual'])}건",
             f"• 판정 실패 배치: {len(report['failed'])}개"]
    if q_fix:
        lines.append("지문 복원: " + ", ".join(q_fix[:200]))
    lines.append("상세: audit/srcverify/report.json")
    return "\n".join(lines)


def publish(summary: str, changed: bool) -> None:
    if changed:
        run = lambda *a, **k: subprocess.run(list(a), cwd=BASE, capture_output=True, text=True, **k)
        run("git", "add", "public/chapters", "src_verify.py", "audit/srcverify/report.json", "audit/srcverify/book_key.json")
        run("git", "commit", "-m", "fix: 교재 원문 전수 대조 — 전사 훼손 지문 원문 복원 및 출처 교정(정답은 교재 정답줄 기준 유지)")
        r = run("npx", "firebase-tools", "deploy", "--only", "hosting", "--project", FIREBASE_PROJECT, timeout=900)
        ok = "Deploy complete" in (r.stdout or "")
        summary += "\n" + ("✅ 배포 완료" if ok else "⚠️ 배포 실패: " + (r.stdout or r.stderr)[-200:])
    telegram_text(summary)


def telegram_text(text: str) -> None:
    env = dict(l.split("=", 1) for l in ENV_FILE.read_text().splitlines() if "=" in l and not l.startswith("#"))
    token, chat = env.get("TELEGRAM_BOT_TOKEN", "").strip(), env.get("ALLOWED_USERS", "").split(",")[0].strip()
    if not token or not chat:
        log("텔레그램 설정 없음 — 알림 생략")
        return
    subprocess.run(["curl", "-s", f"https://api.telegram.org/bot{token}/sendMessage",
                    "-d", f"chat_id={chat}", "--data-urlencode", f"text={text[:3900]}"], capture_output=True, timeout=60)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "scan":
        cmd_scan()
    elif cmd == "judge":
        cmd_judge(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    elif cmd == "apply":
        cmd_apply("--deploy" in sys.argv)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
