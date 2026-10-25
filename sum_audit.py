#!/usr/bin/env python3
"""요약(intros/chNN.json intro·introEasy) 문장 ↔ 교재(유휘운 OCR) 원문 내용 검수.
각 <li>/<p> 항목이 교재·판례·법령과 반대이거나 틀리면 claude(Fable 5.1)가 교정 HTML을 내고 → 자동 적용(백업 audit/sum_bak_<ch>.json),
항목이 바뀌면 그 항목을 가리키던 map 인용(q/eq)도 새 문장 앞 100자로 갱신. 결과 audit/sum_<ch>.json.
  python3 sum_audit.py ch73 [...] | all
사전조건: /tmp/yhw_ocr.txt"""
import json, re, sys
from pathlib import Path

from book_audit import OCR_TXT, squeeze_with_index, region
from intro_audit import CHAP_DIR, INTRO_DIR, claude, parse_json, strip_html

OUT_DIR = Path(__file__).resolve().parent / "audit"
ITEM_RE = re.compile(r"<(li|p)([^>]*)>(.*?)</\1>", re.S)
MAX_QUOTE = 100


def norm(s): return re.sub(r"\s+", " ", s or "").strip()


def items(html):
    """[(전체매치, 내부HTML)] — 문서 순서."""
    return [(m.group(0), m.group(3)) for m in ITEM_RE.finditer(html or "")]


def ask(book, det, easy):
    lst = lambda xs: "\n".join(f"[{i}] {inner}" for i, (_, inner) in enumerate(xs))
    prompt = f"""당신은 행정법 수험서 교정자입니다. [교재 OCR 원문](스캔 OCR이라 글자 오류·줄바꿈 섞임)을 기준으로 [상세 요약 항목]과 [쉬운말 요약 항목]을 검수하세요.
항목 하나하나에 대해 **교재·대법원/헌재 판례·현행 법령에 비추어 내용이 틀렸거나 결론 방향이 반대인 것**만 골라내세요.
- 표현 차이·축약·강조(<b>)·말투는 문제 삼지 않습니다. 교재에 없는 내용이라도 법리상 맞으면 넘어갑니다.
- 틀린 항목은 fix 에 **교정한 항목 내부 HTML**을 씁니다(<b> 태그 등 원래 서식은 최대한 유지, 판례번호가 교재에 있으면 포함). 확신이 없으면 넣지 마세요.
- why 는 한 줄 근거.
파일을 읽거나 수정하지 말고 JSON 하나만 출력: {{"det":[{{"i":3,"why":"...","fix":"..."}}],"easy":[{{"i":5,"why":"...","fix":"..."}}]}}  (없으면 빈 배열)

[교재 OCR 원문]
{book}

[상세 요약 항목]
{lst(det)}

[쉬운말 요약 항목]
{lst(easy)}
"""
    return parse_json(claude(prompt))


def apply(html, its, fixes):
    """fixes[{i,fix}] 적용 → (새 html, [(옛 평문, 새 평문)])."""
    out, changed = html, []
    for f in fixes:
        i, fix = f.get("i"), norm(f.get("fix") or "")
        if not isinstance(i, int) or not (0 <= i < len(its)) or len(fix) < 5: continue
        whole, inner = its[i]
        if out.count(whole) != 1: continue
        out = out.replace(whole, whole.replace(inner, fix))
        changed.append((norm(strip_html(inner)), norm(strip_html(fix)), f.get("why", "")))
    return out, changed


def remap(m, changed, qk):
    """바뀐 항목을 인용하던 map 엔트리의 q/eq 를 새 문장 앞 100자로."""
    new = {}
    for no, e in m.items():
        e2 = dict(e); quote = norm(e.get(qk, ""))
        for old, nw, _ in changed:
            if quote and quote in old: e2[qk] = nw[:MAX_QUOTE]
        new[no] = e2
    return new


def run(ch, raw, ocr, idx):
    path = INTRO_DIR / f"{ch}.json"
    if not path.exists(): return {"ch": ch, "skip": "요약 없음"}
    d = json.load(open(path)); qs = json.load(open(CHAP_DIR / f"{ch}.json"))["questions"]
    book = region(raw, ocr, idx, qs)
    if not book: return {"ch": ch, "error": "교재 위치 못 찾음"}
    det, easy = items(d.get("intro", "")), items(d.get("introEasy", ""))
    r = ask(book, det, easy)
    intro, c1 = apply(d.get("intro", ""), det, r.get("det", []))
    ieasy, c2 = apply(d.get("introEasy", ""), easy, r.get("easy", []))
    m = remap(remap(d.get("map", {}), c1, "q"), c2, "eq")
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"sum_bak_{ch}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1))
    (OUT_DIR / f"sum_{ch}.json").write_text(json.dumps({"ch": ch, "det": c1, "easy": c2, "raw": r}, ensure_ascii=False, indent=1))
    if c1 or c2:
        path.write_text(json.dumps({**d, "intro": intro, "introEasy": ieasy, "map": m}, ensure_ascii=False, indent=1))
    return {"ch": ch, "items": len(det) + len(easy), "fixed_det": len(c1), "fixed_easy": len(c2)}


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args: sys.exit(__doc__)
    raw = OCR_TXT.read_text(); ocr, idx = squeeze_with_index(raw)
    if args == ["all"]: args = sorted(p.stem for p in INTRO_DIR.glob("ch*.json") if (CHAP_DIR / f"{p.stem}.json").exists())
    for c in args:
        try: print(run(c, raw, ocr, idx), flush=True)
        except Exception as ex: print({"ch": c, "error": str(ex)[:200]}, flush=True)
