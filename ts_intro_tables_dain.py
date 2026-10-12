#!/usr/bin/env python3
"""ts01~ts04 요약에 **다인 1200제(소방전술 기출문제집) 해설에 실린 표·정리만** 통합.

배경: ts_intro_tables.py 는 소방학교 공통교재 절 전체의 표를 넣어 요약이 4~5배로 부풀었음.
사용자 지시(2026-09-28): 원본교재 말고 **다인 1200제 해설의 표**만 넣을 것.

흐름 (챕터별):
  1) 1200제 OCR(/tmp/ts_1200.txt) 에서 챕터 문항 src 페이지 범위만 슬라이스
  2) claude가 해설의 ♦ 표·정리만 HTML 표로 추출 (문제 지문·선지는 제외, 숫자 원문 그대로)
  3) 기존 요약(공통교재 표 넣기 전 버전)에 논리적 위치로 삽입 (ts_intro_tables.merge 재사용)
  4) intro_audit 커버리지 검사 후 저장 (기존본 /tmp/intro_backup_dain_tsNN.json)
끝나면 Firebase 배포 + 텔레그램 보고.

사용: python3 ts_intro_tables_dain.py ts01 ts02 ts03 ts04   (반드시 백그라운드 실행)
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intro_audit as ia  # noqa: E402
import ts_intro_tables as tt  # noqa: E402

BOOK_PDF = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/소방전술_ocr.pdf"
BOOK_TXT = Path("/tmp/ts_1200.txt")
BOOK_LAST_PAGE = 660              # ts28 마지막 해설 p.645 까지 포함
LOG_DIR = Path("/tmp/ts_intro_tables")
CHUNK_MAX = 24000
DEPLOY_EVERY = 5                  # N챕터마다 중간 배포

DEDUP_PROMPT = """당신은 소방전술 1타 강사입니다. 챕터 "{title}" 핵심요약에 끼워 넣을 [표 목록]에서 **중복인 표를 골라내세요** (사용자 지시: 요약 본문과 겹치는 표, 표끼리 겹치는 표는 절대 넣지 말 것).

버릴 표(drop):
1. 표의 내용(항목·숫자·구분)이 [요약 본문]에 이미 문장으로 거의 다 적혀 있어 새로 주는 정보가 없는 표
2. 다른 표와 주제·항목이 겹치는 표 — 더 완전한 하나만 남기고 나머지는 drop (예: "A vs B 차이"와 "A·B·C·D 비교"가 같이 있으면 겹치는 쪽 정리)
3. 문제 지문·선지를 그대로 옮긴 것

남길 표(keep): 요약 본문에 없는 숫자 기준·단계·비교를 새로 주는 표.

파일을 읽거나 수정하지 말고 JSON 하나만 출력: {{"drop":[번호,...], "why":{{"번호":"한 줄 이유"}}}} (버릴 표가 없으면 "drop":[]).

[표 목록] (번호 · 제목 · 본문)
{tables}

[요약 본문]
{summary}
"""

EXTRACT_PROMPT = """당신은 소방승진시험(소방전술) 1타 강사입니다.
아래는 **기출문제집(1200제)의 원문**({label})입니다. 각 문제 아래 [해설에 ♦ 표제로 실린 **표·정리·열거 목록**]만 골라 학생용 핵심 표로 정리하세요.

선정 기준:
- 해설의 ♦ 표제 아래에 "구분 | 내용" 식으로 실린 표, 숫자 기준(거리·인원·시간·구경·온도 등), 단계·순서, 종류별 특징 비교 → 전부 포함
- 해설이 문장 한두 줄뿐인 것(예: "④ (X), ~이어야 한다")은 제외
- **문제 지문·선지 자체를 표로 만들지 말 것** — 해설에 실린 정리만
- 같은 표가 여러 문제 해설에 반복되면 한 번만

작성 규칙:
1. 숫자·용어는 해설 원문 그대로. 원문에 없는 내용 창작 금지. OCR 띄어쓰기·오타는 문맥으로 교정.
2. 표 하나마다 <h4>📊 표 제목</h4> 로 시작하고 <table><thead><tr><th>..</th></tr></thead><tbody><tr><td>..</td></tr></tbody></table> 사용. 열은 2~4개.
3. 표로 만들기 어려운 열거는 <ul><li> 로. 정답을 가르는 핵심어·숫자는 <b>로 감싸기.
4. 표 아래 <div class="tip">에 이 표에서 자주 바뀌어 나오는 숫자·헷갈림 포인트를 1~2줄로.
5. 허용 태그: {tags}. 그 외 태그·마크다운 금지. 파일을 읽거나 수정하지 말 것.
6. 표·정리가 하나도 없으면 (없음) 만 출력.
7. 출력 형식 (표식 2개 필수):
<<<TABLES>>>
(HTML)
<<<END_TABLES>>>

[문제집 원문] ({label})
{text}
"""


def load_book() -> list[str]:
    if not BOOK_TXT.exists() or BOOK_TXT.stat().st_size < 100_000:
        subprocess.run(["pdftotext", "-raw", "-f", "1", "-l", str(BOOK_LAST_PAGE), str(BOOK_PDF), str(BOOK_TXT)],
                       check=True, timeout=1800)
    return BOOK_TXT.read_text(errors="ignore").split("\f")   # pdf 쪽번호 N = pages[N-1] = 인쇄 쪽번호 N


def page_range(ch: str) -> tuple[int, int]:
    qs = json.load(open(ia.CHAP_DIR / f"{ch}.json"))["questions"]
    pages = [int(m.group(1)) for q in qs for m in [re.search(r"p\.(\d+)", q.get("src", ""))] if m]
    if not pages:
        raise ValueError(f"{ch}: src 페이지 없음")
    return min(pages), max(pages) + 1              # 마지막 문항 해설이 다음 쪽으로 넘어가는 경우 포함


def slices(ch: str, pages: list[str]) -> list[tuple[str, str]]:
    lo, hi = page_range(ch)
    out, buf, start = [], [], lo
    for n in range(lo, hi + 1):
        buf.append(pages[n - 1])
        if sum(map(len, buf)) >= CHUNK_MAX:
            out.append((f"{ch} p.{start}~{n}", "\n".join(buf)))
            buf, start = [], n + 1
    if buf:
        out.append((f"{ch} p.{start}~{hi}", "\n".join(buf)))
    return out


def extract_tables(label: str, text: str) -> str:
    out = ia.claude(EXTRACT_PROMPT.format(label=label, text=text, tags=tt.TABLE_TAGS))
    m = re.search(r"<<<TABLES>>>\s*(.*?)\s*<<<END_TABLES>>>", out, re.S)
    if not m:
        raise ValueError(f"표 추출 표식 없음 ({label}): {out[:150]}")
    html = m.group(1).strip()
    return "" if html.startswith("(없음)") or "<h4" not in html else html


def dedup(title: str, intro: dict, tables_html: str) -> str:
    """요약 본문과 겹치거나 표끼리 겹치는 표를 제거한 HTML 반환."""
    blocks = tt.table_blocks(tables_html)
    if not blocks:
        return ""
    tl = "\n\n".join(f"[{i}] {b['title']}\n{tt.strip(b['html'])[len(b['title']):][:900]}" for i, b in enumerate(blocks))
    out = ia.parse_json(ia.claude(DEDUP_PROMPT.format(title=title, tables=tl, summary=tt.strip(intro["intro"])[:20000])))
    drop = {int(i) for i in out.get("drop", []) if str(i).isdigit()}
    for i in sorted(drop):
        tt.log(f"  중복 제거 [{i}] {blocks[i]['title']} — {out.get('why', {}).get(str(i), '')}")
    kept = [b["html"] for i, b in enumerate(blocks) if i not in drop]
    if not kept:
        raise ValueError("중복 제거 후 남은 표 없음")
    return "\n".join(kept)


def run(ch: str, pages: list[str]) -> dict:
    chap = json.load(open(ia.CHAP_DIR / f"{ch}.json"))
    qs = [q for q in chap["questions"] if q.get("grade") not in ia.SKIP_GRADES]
    intro_path = ia.INTRO_DIR / f"{ch}.json"
    backup = Path(f"/tmp/intro_backup_dain_{ch}.json")
    intro = json.load(open(intro_path))
    if "📊" in intro["intro"]:
        if not backup.exists():
            raise RuntimeError(f"{ch}: 이미 표가 들어 있고 표 없는 백업도 없음")
        intro = json.load(open(backup))            # 표 넣기 전 버전에서 다시 시작 (중복 제거 재작업)
        tt.log(f"{ch}: 표 없는 백업에서 재작업")
    else:
        shutil.copy(intro_path, backup)

    cache = LOG_DIR / f"{ch}_dain_tables.html"
    if cache.exists() and cache.stat().st_size > 1000:
        all_tables = cache.read_text()
        tt.log(f"{ch}: 표 캐시 재사용 ({all_tables.count('<table')}개)")
    else:
        tables: list[str] = []
        for label, text in slices(ch, pages):
            html = extract_tables(label, text)
            tt.log(f"{ch}: [{label}] 원문 {len(text)}자 → 표 {html.count('<table')}개")
            if html:
                tables.append(html)
        all_tables = "\n".join(tables)
        cache.write_text(all_tables)

    raw_n = all_tables.count("<table")
    all_tables = dedup(chap["title"], intro, all_tables)
    tt.log(f"{ch}: 중복 제거 {raw_n}→{all_tables.count('<table')}개")
    before = len(intro["intro"])
    intro = tt.merge(chap["title"], intro, all_tables)
    tt.log(f"{ch}: 통합 완료 상세 {before}→{len(intro['intro'])}자, 표 {intro['intro'].count('<table')}개")
    remain = tt.coverage(ch, intro, qs)
    intro_path.write_text(json.dumps(intro, ensure_ascii=False, indent=1))
    return {"ch": ch, "tables": intro["intro"].count("<table"), "before": before,
            "after": len(intro["intro"]), "remain": remain}


def main() -> None:
    chs = sys.argv[1:] or ["ts01", "ts02", "ts03", "ts04"]
    LOG_DIR.mkdir(exist_ok=True)
    pages = load_book()
    results = []
    for i, ch in enumerate(chs, 1):
        try:
            results.append(run(ch, pages))
        except Exception as e:                  # 한 챕터 실패가 전체를 멈추지 않도록
            tt.log(f"{ch}: ❌ {e}")
            results.append({"ch": ch, "error": str(e)[:200]})
        if i % DEPLOY_EVERY == 0 and i < len(chs):
            tt.deploy()
            tt.tg(f"📊 전술 요약 표 통합 진행 {i}/{len(chs)} — {ch}까지 배포됨")
    tt.deploy()
    body = "\n".join(f"{r['ch']} ❌ {r['error'][:70]}" if "error" in r else
                     f"{r['ch']} 표 {r['tables']}개 · {r['before']}→{r['after']}자 · 미커버 {r['remain']}"
                     for r in results)
    tt.tg(f"📊 전술 요약에 다인 1200제 해설 표 통합 완료 (중복 제거 적용)\n{body}\n배포 완료 → 앱에서 요약 새로고침 확인")


if __name__ == "__main__":
    main()
