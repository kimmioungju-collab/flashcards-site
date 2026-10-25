#!/usr/bin/env python3
"""문항 ↔ 교재(유휘운 OCR) 원문 전면 대조 검수.
각 챕터 문항이 교재의 같은 번호 지문과 일치하는지(누락·변형), 출처 표기, 책의 ○/× 표시 vs 정답, 해설 모순을
claude(Fable 5.1)가 판정 → audit/book_<ch>.json 기록. 지문 복원·출처 교정은 자동 적용(백업 audit/book_bak_<ch>.json),
정답 불일치·해설 모순은 보고만 한다.
  python3 book_audit.py ch73 [ch74 ...] | all
사전조건: /tmp/yhw_ocr.txt (pdftotext -raw '행정법 유휘운_ocr.pdf')"""
import json, re, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median

from intro_audit import CHAP_DIR, claude, parse_json

OCR_TXT = Path("/tmp/yhw_ocr.txt")
OUT_DIR = Path(__file__).resolve().parent / "audit"
BATCH, WORKERS = 30, 3
MARGIN_BEFORE, MARGIN_AFTER, OUTLIER = 600, 1500, 150_000
SQ = re.compile(r"[\s「」『』()（）\[\]·•,.'\"‘’“”:;!?~\-—○×©®]")


def squeeze_with_index(raw):
    """노이즈 문자 제거한 문자열 + 각 문자의 원문 위치."""
    chars, idx = [], []
    for i, c in enumerate(raw):
        if not SQ.match(c):
            chars.append(c); idx.append(i)
    return "".join(chars), idx


def locate(ocr, q):
    t = SQ.sub("", q["q"])
    for frag in (t[:18], t[10:28], t[-22:-4]):
        if len(frag) >= 12:
            p = ocr.find(frag)
            if p >= 0: return p
    return -1


def region(raw, ocr, idx, qs):
    pos = [p for p in (locate(ocr, q) for q in qs) if p >= 0]
    if not pos: return ""
    m = median(pos)
    kept = [p for p in pos if abs(p - m) <= OUTLIER]
    a, b = idx[min(kept)], idx[min(max(kept) + 1, len(idx) - 1)]
    return raw[max(0, a - MARGIN_BEFORE): b + MARGIN_AFTER]


def ask(book, qs):
    ql = "\n".join(f"[{q['no']}] ans={q.get('ans','')} src={q.get('src','')}\n  q: {q['q']}\n  exp: {re.sub(r'<[^>]+>','',q.get('exp',''))[:200]}" for q in qs)
    prompt = f"""당신은 행정법 수험서 교정자입니다. [교재 OCR 원문]은 스캔 OCR이라 글자 오류(0|→이, 제=→제2조, 아동→이동 등)와 줄바꿈이 섞여 있습니다.
교재는 같은 인쇄번호에 ○지문과 ×지문이 쌍으로 있을 수 있습니다(○는 "©"·"○" 등으로 보임). 각 [문항]을 **같은 번호의, ans와 같은 쪽(ans=O면 ○지문, X면 ×지문, 객관식은 해당 선지)** 교재 지문과 대조하세요.
판정 항목(문항마다):
- text: "same"(뜻·내용 동일, 띄어쓰기·조사·OCR오류 차이는 same) / "truncated"(교재 지문의 일부 절·구가 빠짐) / "altered"(교재와 내용이 다르게 바뀜) / "notfound"(교재에서 해당 번호 지문을 못 찾음)
- full: text가 truncated·altered일 때만, 교재 지문 **전문**을 OCR 오류를 바로잡아 정서법으로 복원한 문장. 그 외 null
- src_book: 교재에 인쇄된 출처 표기(예 "11지방7", "23국가9", "24군무원5", "25변시"). OCR 노이즈(지배→지방, 국超→국가 등)는 상식적으로 교정. 못 찾으면 null
- mark: 교재에서 그 지문의 ○/× 표시가 분명히 읽히면 "O"/"X", 아니면 "unknown"
- exp_conflict: exp가 ans와 반대 방향으로 설명하면 true
확신 없는 것은 same/null/unknown 으로 두세요. 파일을 읽거나 수정하지 말고 JSON 하나만 출력:
{{"r":[{{"no":"01","text":"same","full":null,"src_book":"11지방7","mark":"O","exp_conflict":false}}]}}

[교재 OCR 원문]
{book}

[문항]
{ql}
"""
    items = parse_json(claude(prompt)).get("r", [])
    return {str(it.get("no")): it for it in items if it.get("no") is not None}


def apply(q, it):
    """지문 복원·출처 교정 적용 → (새 문항, 변경목록)."""
    new, ch = dict(q), []
    full = (it.get("full") or "").strip()
    if it.get("text") in ("truncated", "altered") and len(full) >= 10:
        new["q"] = full; ch.append(f"text:{it['text']}")
    sb = (it.get("src_book") or "").strip()
    if sb and re.fullmatch(r"\d{2}[가-힣]{2,4}\d?", sb) and sb != q.get("src", ""):
        new["src"] = sb; ch.append(f"src:{q.get('src','')}→{sb}")
    return new, ch


def run(ch, raw, ocr, idx):
    path = CHAP_DIR / f"{ch}.json"
    data = json.load(open(path)); qs = data["questions"]
    book = region(raw, ocr, idx, qs)
    if not book: return {"ch": ch, "error": "교재 위치 못 찾음"}
    batches = [qs[k:k + BATCH] for k in range(0, len(qs), BATCH)]
    with ThreadPoolExecutor(WORKERS) as ex:
        raw_r = {k: v for r in ex.map(lambda b: ask(book, b), batches) for k, v in r.items()}
    out, fixed, flags = [], [], []
    for q in qs:
        it = raw_r.get(str(q["no"]))
        if not it: out.append(q); continue
        new, chg = apply(q, it)
        if chg: fixed.append({"no": q["no"], "chg": chg, "before": q["q"][:70], "after": new["q"][:120] if "text:" in "".join(chg) else None})
        mark = it.get("mark")
        if mark in ("O", "X") and q.get("ans") in ("O", "X") and mark != q["ans"]: flags.append({"no": q["no"], "kind": "ans≠book", "ans": q["ans"], "book": mark})
        if it.get("exp_conflict"): flags.append({"no": q["no"], "kind": "exp_conflict"})
        if it.get("text") == "notfound": flags.append({"no": q["no"], "kind": "notfound"})
        out.append(new)
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"book_bak_{ch}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (OUT_DIR / f"book_{ch}.json").write_text(json.dumps({"ch": ch, "n": len(qs), "fixed": fixed, "flags": flags, "raw": raw_r}, ensure_ascii=False, indent=1))
    if fixed:
        path.write_text(json.dumps({**data, "questions": out}, ensure_ascii=False, indent=1))
    return {"ch": ch, "n": len(qs), "fixed": len(fixed), "flags": len(flags)}


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args: sys.exit(__doc__)
    raw = OCR_TXT.read_text()
    ocr, idx = squeeze_with_index(raw)
    if args == ["all"]:
        args = sorted(p.stem for p in CHAP_DIR.glob("ch*.json"))
    for c in args:
        try: print(run(c, raw, ocr, idx), flush=True)
        except Exception as ex: print({"ch": c, "error": str(ex)[:200]}, flush=True)
