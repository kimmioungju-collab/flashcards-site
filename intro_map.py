#!/usr/bin/env python3
"""문항 → 핵심요약 위치 매핑 생성 (앱의 "📖 요약에서 보기" 기능용).

각 문항(grade Z 제외)이 상세(intro)·쉬운말(introEasy) 요약의 어느 섹션(<h4>)·어느 문장으로 풀리는지
claude가 판정 → intros/chNN.json 의 "map" 키에 저장.
  map = {"01": {"s": "섹션제목", "q": "요약 원문 인용(≤100자)", "es": "쉬운말 섹션제목", "eq": "쉬운말 인용"}, ...}
인용은 요약 평문의 실제 부분문자열만 인정(검증 실패 시 섹션만 저장).
사용: python3 intro_map.py ch53 [ch54 ...]
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from intro_audit import CHAP_DIR, INTRO_DIR, SKIP_GRADES, claude, parse_json, strip_html
BATCH = 40
WORKERS = 3
MAX_QUOTE = 100


def sections(html: str) -> list[dict]:
    """<h4> 기준으로 요약을 섹션 분할 → [{"title", "text"}]."""
    parts = re.split(r"(?=<h4[^>]*>)", html or "")
    out = []
    for p in parts:
        m = re.match(r"<h4[^>]*>(.*?)</h4>", p, re.S)
        if not m:
            continue
        out.append({"title": norm(strip_html(m.group(1))), "text": norm(strip_html(p))})
    return out


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def sec_block(secs: list[dict]) -> str:
    return "\n".join(f"[{i}] {s['title']}\n{s['text']}" for i, s in enumerate(secs))


def ask(secs: list[dict], easy: list[dict], qs: list[dict]) -> dict:
    ql = "\n".join(f"[{q['no']}] ({q.get('ans','')}) {q['q']}" for q in qs)
    prompt = f"""당신은 행정법 수험 교재 편집자입니다. 학생이 문제를 풀고 나서 "이 문제는 요약의 어디를 보면 되나"를 바로 보여주려 합니다.
[문제] 각각에 대해 정답 근거가 되는 [상세 요약]의 섹션 번호와 그 안의 핵심 문장, 그리고 [쉬운말 요약]의 섹션 번호와 핵심 문장을 고르세요.
규칙:
- quote/equote 는 해당 섹션 본문에서 **글자 그대로 복사한 연속 문자열**(30~{MAX_QUOTE}자). 요약·의역·따옴표 추가 금지. 정답을 가르는 문장을 고르세요.
- 근거가 요약에 없으면 가장 관련 깊은 섹션 번호만 쓰고 quote 는 null.
- 파일을 읽거나 수정하지 말고 JSON 하나만 출력: {{"map":[{{"no":"01","sec":2,"quote":"...","esec":1,"equote":"..."}}]}}

[상세 요약]
{sec_block(secs)}

[쉬운말 요약]
{sec_block(easy) if easy else "(없음)"}

[문제] (번호, 정답, 지문)
{ql}
"""
    items = parse_json(claude(prompt)).get("map", [])
    return {str(it.get("no")): it for it in items if it.get("no") is not None}


def verify_quote(secs: list[dict], sec: int | None, quote: str | None) -> tuple[str | None, str | None]:
    """(섹션제목, 검증된 인용). 인용이 해당 섹션에 없으면 다른 섹션에서 찾고, 그래도 없으면 인용 없이 섹션만."""
    if not secs:
        return None, None
    q = norm(quote or "")[:MAX_QUOTE] if quote else ""
    if q:
        order = ([sec] if isinstance(sec, int) and 0 <= sec < len(secs) else []) + list(range(len(secs)))
        for i in order:
            if q in secs[i]["text"]:
                return secs[i]["title"], q
    if isinstance(sec, int) and 0 <= sec < len(secs):
        return secs[sec]["title"], None
    return None, None


def build(ch: str) -> dict:
    qs = [q for q in json.load(open(CHAP_DIR / f"{ch}.json"))["questions"] if q.get("grade") not in SKIP_GRADES]
    path = INTRO_DIR / f"{ch}.json"
    intro = json.load(open(path))
    secs, easy = sections(intro.get("intro", "")), sections(intro.get("introEasy", ""))
    if not secs:
        raise RuntimeError(f"{ch}: 요약 섹션 없음")
    batches = [qs[i:i + BATCH] for i in range(0, len(qs), BATCH)]
    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(lambda b: ask(secs, easy, b), batches))
    raw = {k: v for r in results for k, v in r.items()}
    m, stats = {}, {"q": 0, "quote": 0, "sec_only": 0, "none": 0}
    for q in qs:
        it = raw.get(str(q["no"]), {})
        s, quote = verify_quote(secs, it.get("sec"), it.get("quote"))
        es, equote = verify_quote(easy, it.get("esec"), it.get("equote"))
        stats["q"] += 1
        if not s:
            stats["none"] += 1
            continue
        stats["quote" if quote else "sec_only"] += 1
        entry = {"s": s}
        if quote: entry["q"] = quote
        if es: entry["es"] = es
        if equote: entry["eq"] = equote
        m[str(q["no"])] = entry
    new = {**intro, "map": m}
    path.write_text(json.dumps(new, ensure_ascii=False, indent=1))
    return {"ch": ch, **stats}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for c in sys.argv[1:]:
        print(build(c), flush=True)
