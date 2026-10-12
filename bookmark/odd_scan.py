"""3단계: 교재 전 쪽 '특이 포인트' 스캔 → odd_marks.jsonl
사용: python3 odd_scan.py [--books 10,11] [--limit N] [--workers N]
"""
import sys
from concurrent.futures import ThreadPoolExecutor

from common import MIN_PAGE_TEXT, ODD_MARKS, OUT_DIR, append_jsonl, claude, load_jsonl, load_pages, parse_json

BATCH = 3
WORKERS = 4

PROMPT = """당신은 소방공무원 승진시험 '소방전술' 출제위원 경험이 있는 전문가입니다.
아래는 중앙소방학교 공통교재(소방전술) 연속 {n}쪽의 원문입니다. 수험생이 교재를 통독하면서 "어? 이건 특이하다, 시험에 낼 만하다" 하고 멈춰야 할 문구에 초록 음영을 치려 합니다.

표시할 것 (쪽당 최대 4개, 정말 눈에 띄는 것만):
- 구체적 수치·기준·시간·거리·비율·온도 (예: "후방 80m 이상", "1/600으로 축소")
- 예외·금지·주의 서술 ("~해서는 안 된다", "단, ~제외", "~와 달리")
- 비교·순서·우선순위 ("A보다 B가 먼저", "가장 ~한 것은")
- 직관과 반대되거나 헷갈리기 쉬운 서술, 용어 정의
- 틀린 보기로 바꾸기 좋은 문장 (숫자·주체·방향을 살짝 바꾸면 함정이 되는 것)

표시하지 말 것: 당연한 일반론, 그림 캡션, 목차, 반복되는 머리글, 긴 서술 통째.

규칙:
- quote는 반드시 원문에서 **연속된 구간을 글자 그대로 복사** (15~100자). 줄바꿈은 공백으로 바꿔도 되지만 단어 변경·생략(…) 금지.
- why는 왜 출제 포인트인지 한 줄(30자 이내).

출력(JSON만):
{{"odd":[{{"page":"쪽ID","quote":"원문 그대로","why":"이유"}}]}}

{pages}
"""


def batches(pages: dict, books: set[str] | None) -> list[list[str]]:
    ids = [k for k, v in pages.items() if v["nlen"] >= MIN_PAGE_TEXT and (books is None or v["book"] in books)]
    out, cur = [], []
    for pid in ids:
        if cur and (pages[cur[-1]]["book"] != pages[pid]["book"] or len(cur) >= BATCH):
            out.append(cur)
            cur = []
        cur.append(pid)
    if cur:
        out.append(cur)
    return out


def process(batch: list[str], pages: dict) -> None:
    blocks = [f"[쪽ID {pid}] (교재 p.{pages[pid]['label'] or '?'} / {pages[pid]['head']})\n{pages[pid]['text'].strip()}"
              for pid in batch]
    prompt = PROMPT.format(n=len(batch), pages="\n\n".join(blocks))
    try:
        data = parse_json(claude(prompt))
        odd = [m for m in data.get("odd", []) if m.get("page") in batch and m.get("quote")]
        rec = {"batch": batch, "odd": odd}
    except Exception as e:  # noqa: BLE001
        rec = {"batch": batch, "odd": [], "error": str(e)[:200]}
    append_jsonl(ODD_MARKS, rec)
    print(batch[0], "~", batch[-1], len(rec["odd"]), "odd", rec.get("error", ""), flush=True)


def opt(name: str, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    books = set(opt("--books", "").split(",")) if "--books" in sys.argv else None
    limit, workers = int(opt("--limit", 0)), int(opt("--workers", WORKERS))
    pages = load_pages()
    done = {pid for r in load_jsonl(ODD_MARKS) if not r.get("error") for pid in r["batch"]}
    todo = [b for b in batches(pages, books) if not all(p in done for p in b)]
    if "--from" in sys.argv:
        sbook, spno = opt("--from", "").split(":")
        todo = [b for b in todo if pages[b[0]]["book"] != sbook or pages[b[0]]["pno"] >= int(spno)]
    if limit:
        todo = todo[:limit]
    print(f"대상 {len(todo)}배치 workers={workers}", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(lambda b: process(b, pages), todo))


if __name__ == "__main__":
    main()
