#!/usr/bin/env python3
"""소방전술(화재진압) 챕터 핵심요약에 교재의 '시험 빈출 표·기준·수치'를 통합.

배경: 기존 ts 요약은 문항 커버 기준으로만 작성되어 교재의 핵심 표(대형·중요·특수화재 기준,
검토회의 대상, 소방력 3요소 수치 등)가 빠져 있음 (사용자 지시 2026-09-27).

흐름 (챕터별):
  1) 소방학교 공통교재(소방전술1 화재1) 원문을 챕터 담당 절(節) 단위로 슬라이스
  2) 절마다 claude가 승진시험 빈출 표·기준·수치를 HTML 표로 추출 (원문 숫자 그대로)
  3) 기존 상세·쉬운말 요약에 표를 통합 (기존 내용 삭제 금지, 논리적 위치에 삽입)
  4) intro_audit 로 문항 커버리지 검사 → 미커버 있으면 교재 원문 근거로 보강
  5) 저장 (기존본은 /tmp/intro_backup_tsNN.json)
전 챕터 끝나면 Firebase 배포 + 텔레그램 보고.

사용: python3 ts_intro_tables.py ts01 ts02 ts03 ts04   (반드시 백그라운드 실행)
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intro_audit as ia  # noqa: E402

ROOT = Path(__file__).resolve().parent
BOOK_PDF = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/전술/10. 소방전술1(화재1).pdf"
BOOK_TXT = Path("/tmp/t10.txt")
ENV_FILE = Path.home() / "telegram-claude-bridge" / ".env"
LOG_DIR = Path("/tmp/ts_intro_tables")
CHUNK_MAX = 28000            # claude 1회 입력 원문 상한(문자)
CHUNK_MIN = 14000            # 이 길이 이상 쌓인 뒤 절 제목에서 끊음
TABLE_TAGS = 'h4, p, ul, li, b, table, thead, tbody, tr, th, td, div class="tip"'

# 챕터 → 교재 절 범위 (BOOK_TXT 줄 번호, 제1편 기준). 절 제목은 슬라이스 라벨용.
CHAPTER_RANGES = {
    "ts01": [(385, 1352)],                                   # 제1장 화재의 의의, 제2장 화재성상
    "ts02": [(1353, 2249), (2250, 4752)],                    # 제3장 전체, 제4장 1~9절
    "ts03": [(1927, 2249), (4753, 6882)],                    # 제3장 6절(안전관리·붕괴), 제4장 10~14절
    "ts04": [(2884, 3043), (6883, 9814)],                    # 제4장 5절 현장지휘, 제5장·제6장
}
SECTION_RE = re.compile(r"^제\d{1,2}[장절] .+")

EXTRACT_PROMPT = """당신은 소방승진시험(소방전술) 출제위원 출신 강사입니다.
아래 [교재 원문]({label})에서 **객관식·OX로 반복 출제되는 표·기준·수치·분류·순서**를 빠짐없이 골라 학생용 핵심 표로 정리하세요.

선정 기준 (해당하면 반드시 포함):
- 숫자 기준표 (인원·금액·거리·시간·온도·압력·비율·개수 등) — 예: 대형·중요·특수화재 구분, 검토회의 대상, 소방력 3요소, 배치기준 거리
- 구분·비교표 (A vs B 장단점, 종류별 특징, 단계·순서, 주체별 역할)
- 교재의 [표 n-n] 로 표시된 표는 전부 포함
- 원문에 "다음과 같다"로 열거된 항목 목록

작성 규칙:
1. 숫자·용어는 원문 그대로. 원문에 없는 내용 창작 금지. 원문이 OCR이라 띄어쓰기 오류가 있으면 문맥으로 교정.
2. 표 하나마다 <h4>📊 표 제목</h4> 로 시작하고 <table><thead><tr><th>..</th></tr></thead><tbody><tr><td>..</td></tr></tbody></table> 사용. 열은 2~4개, 행은 필요한 만큼.
3. 표로 만들기 어려운 목록·순서는 <ul><li> 로. 정답을 가르는 핵심어·숫자는 <b>로 감싸기.
4. 표 아래에 <div class="tip">에 헷갈림 포인트·두문자(있으면)·자주 바뀌어 나오는 숫자를 1~3줄로.
5. 허용 태그: {tags}. 그 외 태그·마크다운 금지. 파일을 읽거나 수정하지 말 것.
6. 출제 가치가 없는 서술형 설명은 제외. 표가 하나도 없으면 (없음) 만 출력.
7. 출력 형식 (표식 2개 필수):
<<<TABLES>>>
(HTML)
<<<END_TABLES>>>

[교재 원문] ({label})
{text}
"""

PLAN_PROMPT = """당신은 소방전술 1타 강사입니다. 챕터 "{title}"의 핵심요약에 교재 빈출 표를 끼워 넣을 **위치만** 정하세요 (본문은 코드가 삽입합니다).

규칙:
- [표 목록]의 표 하나하나에 대해, [상세 요약 섹션]과 [쉬운말 요약 섹션] 중 내용이 가장 관련 깊은 섹션 번호를 고르세요. 표는 그 섹션 **바로 뒤**에 들어갑니다.
- 관련 섹션이 없으면 교재 흐름상 맞는 위치의 섹션 번호를 고르고, 정말 없으면 -1(맨 끝).
- 같은 내용의 표가 둘 이상이면 더 완전한 하나만 남기고 나머지는 "drop": true.
- 파일을 읽거나 수정하지 말고 JSON 하나만 출력: {{"place":[{{"t":0,"after":2,"eafter":1,"drop":false}}, ...]}} (t=표 번호, after=상세 섹션 번호, eafter=쉬운말 섹션 번호). 표 번호는 하나도 빠뜨리지 마세요.

[표 목록] (번호, 제목, 첫 행)
{tables}

[상세 요약 섹션] (번호, 제목, 앞부분)
{secs}

[쉬운말 요약 섹션] (번호, 제목, 앞부분)
{easy}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def tg(text: str) -> None:
    env = dict(l.split("=", 1) for l in ENV_FILE.read_text().splitlines() if "=" in l and not l.startswith("#"))
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = env.get("ALLOWED_USERS", "").split(",")[0].strip()
    subprocess.run(["curl", "-s", f"https://api.telegram.org/bot{token}/sendMessage",
                    "-d", f"chat_id={chat}", "--data-urlencode", f"text={text}"], capture_output=True)


def load_book() -> list[str]:
    if not BOOK_TXT.exists() or BOOK_TXT.stat().st_size < 100_000:
        subprocess.run(["pdftotext", "-raw", str(BOOK_PDF), str(BOOK_TXT)], check=True, timeout=1800)
    return BOOK_TXT.read_text(errors="ignore").splitlines()


def slices(ch: str, lines: list[str]) -> list[tuple[str, str]]:
    """챕터 담당 범위를 절 제목 기준으로 나눈 (라벨, 원문) 목록. 긴 절은 CHUNK_MAX로 분할."""
    # 페이지 머리글(제N장/제N절)이 본문 사이에 줄로 반복되므로, 절 제목은 '충분히 쌓인 뒤' 끊는 기준으로만 사용
    sections: list[tuple[str, str]] = []
    for start, end in CHAPTER_RANGES[ch]:
        label, buf = f"{ch} {start}행~", []
        for ln in lines[start - 1:end]:
            is_head = bool(SECTION_RE.match(ln.strip()))
            if is_head and sum(map(len, buf)) >= CHUNK_MIN:
                sections.append((label, "\n".join(buf)))
                label, buf = ln.strip(), []
            elif is_head and not buf:
                label = ln.strip()
            buf.append(ln)
        if buf:
            sections.append((label, "\n".join(buf)))

    out: list[tuple[str, str]] = []
    for label, txt in sections:
        n = max(1, -(-len(txt) // CHUNK_MAX))
        for i, part in enumerate(split_even(txt, n)):
            out.append((f"{label} ({i+1}/{n})" if n > 1 else label, part))
    return out


def split_even(txt: str, n: int) -> list[str]:
    if n <= 1:
        return [txt]
    size = -(-len(txt) // n)
    return [txt[i:i + size] for i in range(0, len(txt), size)]


def extract_tables(label: str, text: str) -> str:
    out = ia.claude(EXTRACT_PROMPT.format(label=label, text=text, tags=TABLE_TAGS))
    m = re.search(r"<<<TABLES>>>\s*(.*?)\s*<<<END_TABLES>>>", out, re.S)
    if not m:
        raise ValueError(f"표 추출 표식 없음 ({label}): {out[:150]}")
    html = m.group(1).strip()
    return "" if html.startswith("(없음)") or "<h4" not in html else html


def table_blocks(html: str) -> list[dict]:
    """추출 결과를 <h4>📊 단위 블록으로 분할."""
    parts = [p for p in re.split(r"(?=<h4>📊)", html) if p.strip().startswith("<h4>📊")]
    return [{"title": strip(re.match(r"<h4>(.*?)</h4>", p, re.S).group(1)), "html": p.strip()} for p in parts]


def sections(html: str) -> list[str]:
    """요약 HTML을 <h4> 기준 원문 조각으로 분할 (0번은 첫 <h4> 이전 서두)."""
    parts = re.split(r"(?=<h4[^>]*>)", html or "")
    return parts if parts and parts[0].strip() else parts[1:] if parts else [""]


def strip(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h or "")).strip()


def sec_list(secs: list[str]) -> str:
    return "\n".join(f"[{i}] {strip(p)[:160]}" for i, p in enumerate(secs))


def plan(title: str, secs: list[str], easy: list[str], blocks: list[dict]) -> list[dict]:
    tl = "\n".join(f"[{i}] {b['title']} | {strip(b['html'])[len(b['title']):len(b['title'])+120]}"
                    for i, b in enumerate(blocks))
    out = ia.parse_json(ia.claude(PLAN_PROMPT.format(title=title, tables=tl, secs=sec_list(secs), easy=sec_list(easy))))
    place = {int(p["t"]): p for p in out.get("place", []) if "t" in p}
    return [place.get(i, {"t": i, "after": -1, "eafter": -1}) for i in range(len(blocks))]


def insert(secs: list[str], blocks: list[dict], places: list[dict], key: str) -> str:
    """각 섹션 뒤에 배정된 표 블록을 순서대로 삽입."""
    slots: dict[int, list[str]] = {}
    for b, p in zip(blocks, places):
        if p.get("drop"):
            continue
        j = int(p.get(key, -1) if p.get(key) is not None else -1)
        j = j if 0 <= j < len(secs) else len(secs) - 1
        slots.setdefault(j, []).append(b["html"])
    return "\n".join(sec.rstrip() + ("\n" + "\n".join(slots[i]) if i in slots else "") for i, sec in enumerate(secs))


def merge(title: str, intro: dict, tables: str) -> dict:
    blocks = table_blocks(tables)
    secs, easy = sections(intro["intro"]), sections(intro.get("introEasy", ""))
    places = plan(title, secs, easy, blocks)
    kept = sum(1 for p in places if not p.get("drop"))
    new_intro, new_easy = insert(secs, blocks, places, "after"), insert(easy, blocks, places, "eafter")
    if kept == 0 or "<table" not in new_intro or len(new_intro) < len(intro["intro"]):
        raise ValueError(f"통합 결과 이상: 표 {kept}개, 상세 {len(intro['intro'])}→{len(new_intro)}자")
    return {**intro, "intro": new_intro, "introEasy": new_easy}


def coverage(ch: str, intro: dict, qs: list[dict]) -> int:
    """표 삽입은 기존 문장을 지우지 않으므로 커버리지는 검사만 하고 건수를 보고."""
    missing = ia.audit(intro["intro"], qs)
    log(f"{ch}: 통합 후 미커버 {len(missing)}건" + (f" → {[m['no'] for m in missing]}" if missing else ""))
    return len(missing)


def run(ch: str, lines: list[str]) -> dict:
    chap = json.load(open(ia.CHAP_DIR / f"{ch}.json"))
    qs = [q for q in chap["questions"] if q.get("grade") not in ia.SKIP_GRADES]
    intro_path = ia.INTRO_DIR / f"{ch}.json"
    intro = json.load(open(intro_path))
    shutil.copy(intro_path, f"/tmp/intro_backup_{ch}.json")

    cache = LOG_DIR / f"{ch}_tables.html"
    if cache.exists() and cache.stat().st_size > 1000:
        all_tables = cache.read_text()
        log(f"{ch}: 표 캐시 재사용 ({all_tables.count('<table')}개)")
    else:
        tables: list[str] = []
        for label, text in slices(ch, lines):
            html = extract_tables(label, text)
            log(f"{ch}: [{label}] 원문 {len(text)}자 → 표 {html.count('<table')}개")
            if html:
                tables.append(html)
        all_tables = "\n".join(tables)
        cache.write_text(all_tables)

    before = len(intro["intro"])
    intro = merge(chap["title"], intro, all_tables)
    log(f"{ch}: 통합 완료 상세 {before}→{len(intro['intro'])}자, 표 {intro['intro'].count('<table')}개")

    remain = coverage(ch, intro, qs)
    intro_path.write_text(json.dumps(intro, ensure_ascii=False, indent=1))
    return {"ch": ch, "tables": intro["intro"].count("<table"), "before": before,
            "after": len(intro["intro"]), "remain": remain}


def deploy() -> None:
    subprocess.run(["npx", "firebase-tools", "deploy", "--only", "hosting", "--project", "work-schedule-dash-4ceb2"],
                   cwd=ROOT, capture_output=True, timeout=1200)


def main() -> None:
    chs = sys.argv[1:] or list(CHAPTER_RANGES)
    LOG_DIR.mkdir(exist_ok=True)
    lines = load_book()
    results = []
    for ch in chs:
        try:
            r = run(ch, lines)
        except Exception as e:                  # 한 챕터 실패가 전체를 멈추지 않도록
            r = {"ch": ch, "error": str(e)[:200]}
            log(f"{ch}: ❌ {e}")
        results.append(r)
    deploy()
    body = "\n".join(f"{r['ch']} ❌ {r['error'][:70]}" if "error" in r else
                     f"{r['ch']} 표 {r['tables']}개 · {r['before']}→{r['after']}자 · 미커버 {r['remain']}"
                     for r in results)
    tg(f"📊 화재진압 요약에 교재 빈출 표 통합 완료\n{body}\n배포 완료 → 앱에서 요약 새로고침 확인")


if __name__ == "__main__":
    main()
