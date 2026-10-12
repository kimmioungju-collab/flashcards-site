"""2단계: 기출 문항별 교재 근거 문장 추출 → exam_marks.jsonl
사용: python3 map_exam.py [ts01 ts02 ...] [--limit N] [--workers N]
"""
import glob
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from common import CHAPTER_DIR, EXAM_MARKS, OUT_DIR, append_jsonl, claude, load_jsonl, parse_json
from retrieve import PageIndex

TOP_K = 8
WORKERS = 6

PROMPT = """당신은 소방공무원 승진시험 '소방전술' 과목 전문가입니다.
아래는 최승수 기출 1200제의 한 문항이고, 그 아래는 중앙소방학교 공통교재(소방전술)에서 검색된 후보 쪽들의 원문입니다.

목표: 수험생이 교재를 읽을 때 이 문항과 관련된 원문 문장에 음영을 치려 합니다.
1. 정답 보기와 옳은 보기들의 근거가 되는 교재 원문 문장 (kind="ans")
2. 틀린 보기가 비틀어 놓은 교재 원문 문장 — 틀린 보기와 비교해 어디가 다른지 note에 "보기: … → 원문: …" 형식으로 짧게 (kind="wrong")
   (정답이 '옳지 않은 것'이면 정답 보기가 틀린 보기이고, '옳은 것'이면 정답 외 보기들이 틀린 보기입니다)

규칙:
- quote는 반드시 후보 쪽 원문에서 **연속된 구간을 글자 그대로 복사**하세요. 줄바꿈은 공백으로 바꿔도 되지만 단어를 바꾸거나 생략(…)하면 안 됩니다.
- quote는 한 문장~두 문장(20~150자) 정도로, 핵심 수치·조건·용어가 들어가게 잡으세요. 표의 경우 해당 행의 글자들을 연속으로 복사하세요.
- 후보 쪽에 근거가 정말 없으면 그 보기는 비워 두세요. 없는 문장을 지어내지 마세요.
- 같은 문장이 여러 쪽에 중복이면 가장 본문다운 한 곳만.
- 보기 4개 전부에 대해 가능한 한 근거를 찾으세요(최대 8개).

출력(JSON만):
{{"marks":[{{"page":"쪽ID","quote":"원문 그대로","kind":"ans|wrong","opt":"①|②|③|④","note":"짧은 메모(wrong은 보기→원문 차이)"}}]}}

### 문항 [{qid}] ({theme})
{q}
정답: {ans}
해설: {exp}

### 교재 후보 쪽
{pages}
"""


def load_questions(chapters: list[str]) -> list[dict]:
    out = []
    files = sorted(glob.glob(str(CHAPTER_DIR / "ts*.json")))
    for f in files:
        ch = f.split("/")[-1][:-5]
        if ch == "ts29" or (chapters and ch not in chapters):
            continue
        for q in json.load(open(f))["questions"]:
            out.append({"qid": f"{ch}-{q['no']}", **q})
    return out


def build_prompt(q: dict, idx: PageIndex) -> tuple[str, list[str]]:
    query = f"{q['theme']} {q['q']} {q['exp']}"
    hits = [pid for pid, _ in idx.search(query, TOP_K)]
    blocks = []
    for pid in hits:
        p = idx.pages[pid]
        blocks.append(f"[쪽ID {pid}] (교재 p.{p['label'] or '?'} / {p['head']})\n{p['text'].strip()}")
    prompt = PROMPT.format(qid=q["qid"], theme=q["theme"], q=q["q"], ans=q["ans"], exp=q["exp"],
                           pages="\n\n".join(blocks))
    return prompt, hits


def process(q: dict, idx: PageIndex) -> dict:
    prompt, hits = build_prompt(q, idx)
    try:
        data = parse_json(claude(prompt))
        marks = [m for m in data.get("marks", []) if m.get("page") in hits and m.get("quote")]
        rec = {"qid": q["qid"], "ans": q["ans"], "hits": hits, "marks": marks}
    except Exception as e:  # noqa: BLE001 — 실패도 기록해 재시도 가능하게
        rec = {"qid": q["qid"], "ans": q["ans"], "hits": hits, "marks": [], "error": str(e)[:200]}
    append_jsonl(EXAM_MARKS, rec)
    print(q["qid"], len(rec["marks"]), "marks", rec.get("error", ""), flush=True)
    return rec


def main() -> None:
    args = [a for a in sys.argv[1:] if a.startswith("ts")]
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else WORKERS
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = {r["qid"] for r in load_jsonl(EXAM_MARKS) if not r.get("error")}
    qs = [q for q in load_questions(args) if q["qid"] not in done]
    if limit:
        qs = qs[:limit]
    idx = PageIndex()
    print(f"대상 {len(qs)}문항 (완료 {len(done)}) workers={workers}", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(lambda q: process(q, idx), qs))


if __name__ == "__main__":
    main()
