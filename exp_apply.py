"""exp_verify 결과(audit/expverify/report.json)의 지적 문항을 교재 원문 해설로 교체.
  python3 exp_apply.py [--deploy]
각 지적마다 claude가 해당 교재 페이지에서 원문(요약·지문)을 전사해 exp 작성 → OCR 글자 대조(≥80%)·kwr⊂q·kw2 유일성 통과분만 적용."""
import json, re, sys, subprocess, difflib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from src_verify import norm, claude, parse_json, telegram_text, log
from exp_verify import PagedOcr, OCR_TXT, CH_DIR, OUT

BASE = Path(__file__).resolve().parent
COV_MIN, WORKERS, PAGE_CAP = 0.8, 3, 7000
FIREBASE_PROJECT = "work-schedule-dash-4ceb2"

PROMPT = """당신은 행정법 기출 OX 문제집 검수자입니다. (B)는 교재 한 페이지 OCR 원문(2단: 왼쪽 판서 요약은 줄 끝 첨자=문항번호, 오른쪽 문항은 ○/× 표식+번호, 정답표 줄 포함)입니다.
(A) 앱 문항의 해설(exp)이 아래 지적을 받았습니다. 교재를 다시 보고 판정하세요.
[지적] {detail}
1. verdict: "fix"(해설이 교재와 다르거나 핵심을 놓침) 또는 "keep"(해설이 교재와 부합)
2. fix일 때 new_exp: 교재 (B)에 실제로 적힌 요약 줄·원문 지문을 **그대로 전사**해 1~3문장으로 작성. OCR 오인식·띄어쓰기만 바로잡고 표현은 바꾸지 말 것. 첨자 번호는 제거. 교재에 없는 문장은 절대 추가 금지. 필요하면 "※ 법리:" 같은 교재 소제목도 그대로 포함.
3. kwr: ans가 X 또는 ①~⑤이면 q 안에서 틀린 원인이 되는 구절(q에 글자 그대로 들어있는 부분문자열) 1~2개. ans가 O이면 빈 배열.
4. kw2: new_exp 안의 핵심 구절(new_exp에 정확히 1회만 등장하는 부분문자열) 1~3개.
반드시 아래 JSON만 출력:
{"verdict":"fix|keep","new_exp":"","kwr":[],"kw2":[],"note":"한 줄"}

(A) [{ch}] no={no} ans={ans}
q: {q}
exp: {exp}

(B) 교재 OCR 원문 (p.{page})
{page_text}"""


def coverage(text: str, page: str) -> float:
    a, b = norm(text), norm(page)
    if not a:
        return 0.0
    blocks = [m for m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks() if m.size >= 4]
    return sum(m.size for m in blocks) / len(a)


def verify(q: dict, r: dict, page: str) -> str:
    exp, kwr, kw2 = (r.get("new_exp") or "").strip(), r.get("kwr") or [], r.get("kw2") or []
    if len(exp) < 10:
        return "new_exp 없음"
    cov = coverage(exp, page)
    if cov < COV_MIN:
        return f"원문 일치율 {cov:.2f} < {COV_MIN}"
    if q["ans"] == "O" and kwr:
        return "O문항인데 kwr 있음"
    if any(k not in q["q"] for k in kwr):
        return "kwr가 q에 없음"
    if any(exp.count(k) != 1 for k in kw2):
        return "kw2가 exp에 유일하지 않음"
    return ""


def judge(ocr: PagedOcr, ch: str, q: dict, issue: dict) -> dict:
    page = ocr.pages[issue["page"] - 1][:PAGE_CAP]
    prompt = PROMPT
    for k, v in {"detail": issue.get("detail", ""), "ch": ch, "no": q["no"], "ans": q["ans"], "q": q["q"],
                 "exp": q.get("exp", ""), "page": str(issue["page"]), "page_text": page}.items():
        prompt = prompt.replace("{" + k + "}", str(v))
    base = {"ch": ch, "no": q["no"], "type": issue["type"], "conf": issue["confidence"]}
    for attempt in range(2):
        try:
            r = parse_json(claude(prompt))
            if r.get("verdict") != "fix":
                return {**base, "status": "keep", "note": r.get("note", "")}
            why = verify(q, r, page)
            if why:
                return {**base, "status": "manual", "note": why, "new_exp": r.get("new_exp", "")}
            return {**base, "status": "apply", "new_exp": r["new_exp"].strip(), "kwr": r.get("kwr") or [],
                    "kw2": r.get("kw2") or [], "note": r.get("note", "")}
        except Exception as e:
            log(f"{ch}:{q['no']} 실패({attempt+1}): {str(e)[:100]}")
    return {**base, "status": "manual", "note": "claude 응답 실패"}


def apply(results: list) -> list:
    changed = []
    for ch in sorted({r["ch"] for r in results if r["status"] == "apply"}):
        p = CH_DIR / f"{ch}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        (OUT / f"bak_{ch}.json").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        fixes = {r["no"]: r for r in results if r["ch"] == ch and r["status"] == "apply"}
        qs = []
        for q in d["questions"]:
            f = fixes.get(q["no"])
            if not f:
                qs.append(q); continue
            nq = {k: v for k, v in q.items() if k not in ("kwr", "kw2")}
            nq["exp"] = f["new_exp"]
            if q["ans"] != "O":
                nq["kwr"], nq["kw2"] = f["kwr"], f["kw2"]
            qs.append(nq)
        p.write_text(json.dumps({**d, "questions": qs}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        changed.append(ch)
    return changed


def main() -> None:
    report = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
    ocr = PagedOcr(OCR_TXT)
    jobs = []
    for ch, issues in report.items():
        qmap = {q["no"]: q for q in json.loads((CH_DIR / f"{ch}.json").read_text(encoding="utf-8"))["questions"]}
        jobs += [(ch, qmap[str(x["no"])], x) for x in issues if str(x["no"]) in qmap]
    log(f"교체 판정 {len(jobs)}건")
    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(lambda j: judge(ocr, *j), jobs))
    (OUT / "apply_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    changed = apply(results)
    ap = [r for r in results if r["status"] == "apply"]
    keep = [r for r in results if r["status"] == "keep"]
    man = [r for r in results if r["status"] == "manual"]
    msg = [f"🛠 해설 교체 결과 (지적 {len(results)}건)", f"✅ 교재 원문으로 교체 {len(ap)}건: " + ", ".join(f"{r['ch']}-{r['no']}" for r in ap),
           f"↔ 재검토 결과 기존 해설 유지 {len(keep)}건: " + ", ".join(f"{r['ch']}-{r['no']}" for r in keep),
           f"⚠️ 수동 확인 필요 {len(man)}건: " + ", ".join(f"{r['ch']}-{r['no']}({r['note'][:12]})" for r in man)]
    if changed and "--deploy" in sys.argv:
        run = lambda *a, **k: subprocess.run(list(a), cwd=BASE, capture_output=True, text=True, **k)
        run("git", "add", "public/chapters", "exp_apply.py", "exp_verify.py", "audit/expverify")
        run("git", "commit", "-m", "fix: 해설 전수 검수 지적 문항 교재 원문 해설로 교체\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>")
        run("git", "push", "origin", "HEAD")
        r = run("npx", "firebase-tools", "deploy", "--only", "hosting", "--project", FIREBASE_PROJECT, timeout=900)
        msg.append("🚀 배포 완료" if "Deploy complete" in (r.stdout or "") else "⚠️ 배포 실패: " + (r.stdout or r.stderr)[-200:])
    text = "\n".join(msg)
    print(text)
    telegram_text(text)


if __name__ == "__main__":
    main()
