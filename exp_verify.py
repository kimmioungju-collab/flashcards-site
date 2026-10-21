"""교재 본문(요약·정답) 대조 해설 검수: ch01~ch50, 기본등급('기')·무등급('-') 제외.
  python3 exp_verify.py run      # 페이지 단위로 claude 판정 → audit/expverify/*.json (재실행 시 완료분 건너뜀)
  python3 exp_verify.py report   # 집계 → audit/expverify/report.md + 텔레그램 전송
교재 OCR: pdftotext -raw 결과(/tmp/haengjeong_ocr.txt, \\f로 페이지 구분)."""
import json, os, re, sys, time, bisect
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from src_verify import Ocr, norm, claude, parse_json, telegram_text, log

BASE = Path(__file__).resolve().parent
CH_DIR = BASE / "public" / "chapters"
OUT = BASE / "audit" / "expverify"
OCR_TXT = Path(os.environ.get("EXPV_OCR", "/tmp/haengjeong_ocr.txt"))
CH_RANGE = range(1, 51)
SKIP_GRADES = {"-", "기"}
COV_MIN = 0.6
WORKERS, RETRY = 3, 2
PAGE_CAP = 7000

PROMPT = """당신은 행정법 기출 OX 문제집 검수자입니다. (B)는 교재 한 페이지의 OCR 원문입니다. 2단 편집이라 왼쪽 열의 '판서 요약'(핵심 정리, 각 줄 끝에 첨자 번호=문항번호)과 오른쪽 열의 '문항'(앞에 ○/×/기/소 표식과 번호)이 섞여 있고, 'O 61 X O 63 ...' 같은 정답표 줄과 OCR 오인식이 있습니다.
(A)는 이 페이지에 있는 플래시카드 앱 문항(번호=교재 문항번호)입니다.
각 문항마다 (B)의 요약·정답표·해당 문항 표식을 근거로 검수하세요.
 1. 해설오류: exp가 교재 요약·판례·조문과 반대이거나 틀린 법리를 말함 (예: 교재는 '기각(→각하×)'인데 exp가 '위헌 여부 판단해야'라고 함)
 2. 정답오류: ans가 교재의 ○/× 표식·정답표와 다름
 3. 해설부실: exp가 X지문의 틀린 지점(교재가 짚는 핵심)을 전혀 설명하지 못함 — 확실할 때만
문제 없는 문항은 절대 언급하지 마세요. 교재 원문에 없는 내용을 지어내지 마세요. 애매하면 confidence를 "mid"로.
fix_exp는 교재 요약·원문 표현을 그대로 살려 1~2문장으로 쓰세요(X지문이면 틀린 지점과 맞는 내용).
반드시 아래 JSON만 출력:
{"issues":[{"no":"문항no","type":"해설오류|정답오류|해설부실","confidence":"high|mid","book":"교재 근거 인용(짧게)","detail":"한두 문장","fix_exp":"필요시 고친 해설"}]}
문제가 없으면 {"issues":[]}

(A) 앱 문항 [{ch} {title}]
{items}

(B) 교재 OCR 원문 (p.{page})
{page_text}"""


class PagedOcr(Ocr):
    def __init__(self, path: Path):
        super().__init__(path)
        self.breaks = [i for i, c in enumerate(self.raw) if c == "\f"]
        self.pages = self.raw.split("\f")

    def page_of(self, nidx: int) -> int:
        return bisect.bisect_right(self.breaks, self.pos[min(nidx, len(self.pos) - 1)])


def load_targets() -> dict:
    out = {}
    for n in CH_RANGE:
        p = CH_DIR / f"ch{n:02d}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        qs = [q for q in d["questions"] if q.get("grade") not in SKIP_GRADES]
        if qs:
            out[p.stem] = {"title": d["title"], "questions": qs}
    return out


def locate_all(ocr: PagedOcr, targets: dict) -> tuple[dict, list]:
    groups, missing = defaultdict(list), []
    for ch, d in targets.items():
        for q in d["questions"]:
            qt = q["q"].split("\n\n")[-1] if "\n\n" in q["q"] else q["q"]
            cov, first, _ = ocr.cover(norm(qt)[:300])
            if first is None or cov < COV_MIN:
                missing.append(f"{ch}:{q['no']}")
                continue
            groups[(ch, ocr.page_of(first))].append(q)
    return groups, missing


def fmt(q: dict) -> str:
    return f"[no={q['no']}] ans={q['ans']} grade={q.get('grade','')}\nq: {q['q']}\nexp: {q.get('exp','')}\n"


def judge_group(ocr: PagedOcr, key: tuple, qs: list, title: str) -> None:
    ch, page = key
    out = OUT / f"{ch}_p{page:03d}.json"
    if out.exists():
        return
    text = ocr.pages[page][:PAGE_CAP]
    prompt = (PROMPT.replace("{ch}", ch).replace("{title}", title).replace("{page}", str(page + 1))
              .replace("{items}", "\n".join(fmt(q) for q in qs)).replace("{page_text}", text))
    nos = {q["no"] for q in qs}
    for attempt in range(RETRY + 1):
        try:
            issues = [x for x in parse_json(claude(prompt)).get("issues", []) if str(x.get("no")) in nos]
            out.write_text(json.dumps({"ch": ch, "page": page + 1, "nos": sorted(nos), "issues": issues},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            log(f"{ch} p{page+1}: {len(qs)}문항 지적 {len(issues)}")
            return
        except Exception as e:
            log(f"{ch} p{page+1} 실패({attempt+1}): {str(e)[:120]}")
            time.sleep(15)
    (OUT / f"failed_{ch}_p{page:03d}.txt").write_text(",".join(sorted(nos)), encoding="utf-8")


def cmd_run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ocr, targets = PagedOcr(OCR_TXT), load_targets()
    groups, missing = locate_all(ocr, targets)
    (OUT / "missing.json").write_text(json.dumps(missing, ensure_ascii=False), encoding="utf-8")
    total = sum(len(v) for v in groups.values())
    log(f"대상 {total + len(missing)}문항 / 페이지그룹 {len(groups)} / 위치 못 찾음 {len(missing)}")
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda kv: judge_group(ocr, kv[0], kv[1], targets[kv[0][0]]["title"]), sorted(groups.items())))
    cmd_report()


def cmd_report() -> None:
    res = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(OUT.glob("ch*_p*.json"))]
    missing = json.loads((OUT / "missing.json").read_text(encoding="utf-8")) if (OUT / "missing.json").exists() else []
    failed = len(list(OUT.glob("failed_*.txt")))
    checked = sum(len(r["nos"]) for r in res)
    by_ch = defaultdict(list)
    for r in res:
        for x in r["issues"]:
            by_ch[r["ch"]].append({**x, "page": r["page"]})
    high = [(ch, x) for ch, xs in by_ch.items() for x in xs if x.get("confidence") == "high"]
    mid = sum(1 for xs in by_ch.values() for x in xs if x.get("confidence") != "high")
    lines = [f"📋 해설 검수 결과 (ch01~50, 기본·무등급 제외)",
             f"검수 {checked}문항 / 위치미확인 {len(missing)} / 실패배치 {failed}",
             f"지적: high {len(high)}건, mid {mid}건", ""]
    for ch in sorted(by_ch):
        hs = [x for x in by_ch[ch] if x.get("confidence") == "high"]
        if hs:
            lines.append(f"▪ {ch}: " + ", ".join(f"{x['no']}({x['type'][:2]})" for x in hs))
    md = ["# 해설 검수 리포트", ""]
    for ch in sorted(by_ch):
        md.append(f"## {ch}")
        for x in sorted(by_ch[ch], key=lambda x: (x.get("confidence") != "high", x["no"])):
            md.append(f"- **{x['no']}** [{x['type']}/{x['confidence']}] p.{x['page']} — {x.get('detail','')}\n"
                      f"  - 교재: {x.get('book','')}\n  - 수정안: {x.get('fix_exp','')}")
    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")
    (OUT / "report.json").write_text(json.dumps(dict(by_ch), ensure_ascii=False, indent=1), encoding="utf-8")
    text = "\n".join(lines)
    print(text)
    telegram_text(text + f"\n\n상세: audit/expverify/report.md (총 {sum(len(v) for v in by_ch.values())}건)")


if __name__ == "__main__":
    {"run": cmd_run, "report": cmd_report}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
