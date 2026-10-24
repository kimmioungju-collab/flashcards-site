#!/usr/bin/env python3
"""문항 ↔ 요약 근거(map) 의미 검수: intros/chNN.json 의 map[문항] 인용이 실제로 그 문항의 정답 근거인지
claude(Fable 5.1)가 판정 → 틀린 것은 요약 항목(li/p) 중에서 다시 고르고, 결과를 audit/map_<ch>.json 에 기록.
  python3 map_audit.py ch01 [ch02 ...]        # 판정 + 즉시 적용 (백업 audit/map_bak_<ch>.json)
  python3 map_audit.py all                    # 전 챕터
인용(q/eq)은 항상 해당 항목 평문의 앞 100자(= 앱이 li 하나를 집어내는 부분문자열)."""
import json, re, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from intro_audit import CHAP_DIR, INTRO_DIR, SKIP_GRADES, claude, parse_json, strip_html

OUT_DIR = Path(__file__).resolve().parent / "audit"
BATCH, WORKERS, MAX_QUOTE, MAX_EXP = 25, 3, 100, 220


def norm(s): return re.sub(r"\s+", " ", s or "").strip()


def sections(html):
    """[{"title", "items":[평문,...]}] — 앱(sumSectionHtml)과 같은 li/p 단위."""
    out = []
    for p in re.split(r"(?=<h4[^>]*>)", html or ""):
        m = re.match(r"<h4[^>]*>(.*?)</h4>", p, re.S)
        if not m: continue
        items = [norm(strip_html(x)) for x in re.findall(r"<(?:li|p)[^>]*>(.*?)</(?:li|p)>", p[m.end():], re.S)]
        out.append({"title": norm(strip_html(m.group(1))), "items": [i for i in items if i]})
    return out


def block(secs):
    return "\n".join(f"[{i}] {s['title']}\n" + "\n".join(f"  [{i}.{j}] {t}" for j, t in enumerate(s["items"]))
                     for i, s in enumerate(secs))


def locate(secs, title, quote):
    """현재 map 항목이 가리키는 (섹션번호, 항목번호). 항목을 못 찾으면 j=None."""
    for i, s in enumerate(secs):
        if s["title"] != title: continue
        for j, t in enumerate(s["items"]):
            if quote and quote in t: return i, j
        return i, None
    return None, None


def ask(secs, easy, qs, cur):
    ql = "\n".join(f"[{q['no']}] 정답={q.get('ans','')} | 현재근거: 상세 {cur[q['no']][0]} / 쉬운말 {cur[q['no']][1]}\n"
                   f"  문제: {q['q']}\n  교재해설: {norm(strip_html(q.get('exp','')))[:MAX_EXP]}" for q in qs)
    prompt = f"""당신은 행정법 수험 교재 편집자입니다. 각 [문제]에는 "현재근거"로 요약의 항목 번호가 붙어 있습니다.
그 항목이 정말로 이 문제의 정답을 가르는 근거 문장인지 검수하세요.
규칙:
- 현재근거 항목이 문제의 쟁점(같은 개념·같은 판례·같은 요건)을 직접 다루면 ok=true.
- 다른 쟁점을 다루거나, 같은 섹션이라도 더 정확한 항목이 따로 있으면 ok=false 로 하고 pick 에 가장 알맞은 항목 번호("섹션.항목")를 쓰세요.
- 현재근거가 "없음"이면 ok=false 로 하고 pick 을 고르세요. 요약에 근거가 전혀 없으면 pick=null.
- 상세 요약(pick)과 쉬운말 요약(epick)을 각각 판정합니다. 쉬운말 요약이 "(없음)"이면 eok=true, epick=null.
- 파일을 읽거나 수정하지 말고 JSON 하나만 출력:
  {{"r":[{{"no":"01","ok":true,"pick":null,"eok":false,"epick":"2.1"}}]}}

[상세 요약]
{block(secs)}

[쉬운말 요약]
{block(easy) if easy else "(없음)"}

[문제]
{ql}
"""
    items = parse_json(claude(prompt)).get("r", [])
    return {str(it.get("no")): it for it in items if it.get("no") is not None}


def pick_entry(secs, ref):
    """"i.j" → (섹션제목, 인용) / 잘못된 번호면 None."""
    m = re.fullmatch(r"(\d+)\.(\d+)", str(ref or ""))
    if not m: return None
    i, j = int(m.group(1)), int(m.group(2))
    if i >= len(secs) or j >= len(secs[i]["items"]): return None
    return secs[i]["title"], secs[i]["items"][j][:MAX_QUOTE]


def run(ch):
    path = INTRO_DIR / f"{ch}.json"
    intro = json.load(open(path))
    qs = {str(q["no"]): q for q in json.load(open(CHAP_DIR / f"{ch}.json"))["questions"] if q.get("grade") not in SKIP_GRADES}
    secs, easy = sections(intro.get("intro", "")), sections(intro.get("introEasy", ""))
    old = intro.get("map", {})
    targets, cur = [], {}
    for no, m in old.items():
        if no not in qs: continue
        i, j = locate(secs, norm(m.get("s")), norm(m.get("q")))
        ei, ej = locate(easy, norm(m.get("es")), norm(m.get("eq")))
        cur[no] = (f"{i}.{j}" if j is not None else "없음", f"{ei}.{ej}" if ej is not None else ("없음" if easy else "-"))
        targets.append(qs[no])
    batches = [targets[k:k + BATCH] for k in range(0, len(targets), BATCH)]
    with ThreadPoolExecutor(WORKERS) as ex:
        raw = {k: v for r in ex.map(lambda b: ask(secs, easy, b, cur), batches) for k, v in r.items()}
    new, changes = dict(old), []
    for no in cur:
        it = raw.get(no)
        if not it: continue
        e = dict(old[no])
        if not it.get("ok", True):
            p = pick_entry(secs, it.get("pick"))
            if p: e["s"], e["q"] = p
        if easy and not it.get("eok", True):
            p = pick_entry(easy, it.get("epick"))
            if p: e["es"], e["eq"] = p
        if e != old[no]:
            changes.append({"no": no, "before": cur[no], "pick": it.get("pick"), "epick": it.get("epick"), "q": qs[no]["q"][:60]})
            new[no] = e
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"map_bak_{ch}.json").write_text(json.dumps(old, ensure_ascii=False, indent=1))
    (OUT_DIR / f"map_{ch}.json").write_text(json.dumps({"ch": ch, "n": len(cur), "changes": changes}, ensure_ascii=False, indent=1))
    if changes:
        path.write_text(json.dumps({**intro, "map": new}, ensure_ascii=False, indent=1))
    return {"ch": ch, "n": len(cur), "changed": len(changes)}


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args: sys.exit(__doc__)
    if args == ["all"]:
        args = sorted(p.stem for p in INTRO_DIR.glob("ch*.json"))
    for c in args:
        try: print(run(c), flush=True)
        except Exception as ex: print({"ch": c, "error": str(ex)[:200]}, flush=True)
