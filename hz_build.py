#!/usr/bin/env python3
"""위험물안전관리법(문옥섭, 시대에듀 2026) '빨간키(빨리보는 간단한 키워드)' → 위험물 OX 플래너 챕터(hzNN).

사용자 지시(2026-09-13): "위험물 OX 플래너 만들자. 특히 빨간키로 만들어줘"
- 핵심요약(intro) = 빨간키 원문을 HTML 로 복원 (요약·의역 금지, fs_summary_html.py 와 같은 규칙)
- 문제 = 빨간키 문장 기반 OX. X지문은 숫자·주체·기간·요건 하나만 바꾸고 kwr(빨간 음영), exp 는 O원문 + kw2

사용법:
  python3 hz_build.py hz01            # 1개 챕터 생성 → public/chapters/hz01.json, public/intros/hz01.json, manifest
  python3 hz_build.py hz01 --dry      # claude 호출 없이 원문 분할만 확인
원문: /tmp/hz_full.txt (pdftotext -raw ~/files/위험물_ocr.pdf)
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from add_chapter import update_manifest, update_qgrades  # noqa: E402
from fs_summary_html import convert as intro_convert, validate as intro_validate  # noqa: E402

BASE = Path(__file__).resolve().parent
SRC = Path("/tmp/hz_full.txt")
WORK = Path("/tmp/hz_build")
MODEL = "opus"
CLAUDE_TIMEOUT = 1500
PART_CHARS = 6500                      # claude 1회 입력 상한(문자) — 긴 장은 나눠서 생성
Q_PER_HANGUL = 110                     # 한글 N자당 문제 1개
MIN_Q_PER_PART = 20
SRC_LABEL = "위험물안전관리법(문옥섭) 빨간키"

# (id, 제목, 시작줄, 끝줄) — /tmp/hz_full.txt 1-based inclusive
CHAPTERS = [
    ("hz01", "총칙", 492, 841),
    ("hz02", "위험물시설의 설치 및 변경", 842, 1258),
    ("hz03", "위험물시설의 안전관리", 1259, 1875),
    ("hz04", "위험물의 운반 등", 1876, 1970),
    ("hz05", "감독 및 조치명령", 1971, 2076),
    ("hz06", "보칙", 2077, 2259),
    ("hz07", "벌칙 및 기간정리", 2260, 2633),
    ("hz08", "위험물 제조소의 위치·구조 및 설비의 기준", 2634, 2998),
    ("hz09", "위험물 저장소", 2999, 3816),
    ("hz10", "위험물 취급소", 3817, 4327),
    ("hz11", "소화설비·경보설비 및 피난설비의 기준", 4328, 4803),
    ("hz12", "위험물의 저장·취급 및 운반기준", 4804, 5192),
    ("hz13", "혼동하기 쉬운 내용 학습정리", 5193, 5743),
]
NOISE = re.compile(r"진격의 소방|저자가 운영하는|화재감식평가기사|소방 공무원법\)시험과|소방승진 위험물안전관리법|빨리보는 간단한 키워드|^빨 리보는|합격의 공식|시대에듀|^소방승진$|^\d+ 소방승진")

Q_PROMPT = """아래는 소방승진 시험 교재 『위험물안전관리법』의 '빨간키(핵심 키워드 요약)' 원문입니다.
이 원문만을 근거로 OX 문제를 JSON 배열로 만드세요. 목표 문항 수: 약 {n}개 (O와 X를 절반씩).

[문항 작성 규칙]
- 원문에 있는 사실만 출제. 원문에 없는 내용·추측 금지. 숫자·기간·주체·요건·수량·거리·품명 등 시험에 나오는 포인트를 빠짐없이 골고루 다룹니다.
- 각 문항은 하나의 완결된 서술문(평서문)으로 씁니다. "다음 중", "옳은 것은" 같은 객관식 표현 금지.
- ans "O" 문항: q 는 원문 문장을 그대로(또는 표 항목을 문장으로 풀어) 씁니다. kw = q 안의 핵심 키워드 하나(q 안에서 단 한 번만 나오는 어구).
- ans "X" 문항: q 는 원문 문장에서 숫자·주체·기간·요건·품명 중 **딱 하나만** 바꾼 문장. kwr = 바꾼 부분(q 안에서 단 한 번만 나오는 어구, 빨간 음영용).
  exp = 원문의 올바른 문장을 **그대로 전사**(요약·의역 금지) + 필요하면 한 줄 포인트. kw2 = exp 안의 정답 키워드(exp 안에서 단 한 번만 나오는 어구).
- grade: 원문 그 부분에 "NN년 소방위" 표기가 있으면 "위", "소방장"만 있으면 "장", 둘 다 있으면 "위", 없으면 "-".
- theme: 해당 소제목(예: 용어의 정의, 지정수량, 변경허가 대상). 짧게.
- OCR 오류(• → ·, （）→ (), 깨진 기호)는 바로잡되 내용은 바꾸지 마세요.

[출력 형식 — JSON 배열만, 코드펜스·설명 금지]
[{{"q":"...","ans":"O","kw":"...","exp":"...","grade":"위","theme":"..."}},
 {{"q":"...","ans":"X","kwr":"...","exp":"...","kw2":"...","grade":"-","theme":"..."}}]
"""


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def hangul(text: str) -> int:
    return len(re.findall(r"[가-힣]", text))


def chapter_src(start: int, end: int) -> str:
    lines = SRC.read_text(encoding="utf-8").split("\n")[start - 1:end]   # splitlines() 는 \f(페이지) 도 줄로 세어 번호가 어긋남
    return "\n".join(ln.replace("\f", "") for ln in lines if ln.strip() and not NOISE.search(ln))


def split_parts(src: str) -> list[str]:
    """PART_CHARS 이하로 나눔. 가능하면 '숫자)' '(n)' 'n ' 같은 항목 시작줄에서 끊는다."""
    if len(src) <= PART_CHARS:
        return [src]
    parts, cur = [], []
    for ln in src.splitlines():
        if sum(map(len, cur)) > PART_CHARS * 0.8 and re.match(r"^(\(?\d+\)|[①-⑳]|[a-zA-Z가-힣]\s?\d|\d+\.)", ln):
            parts.append("\n".join(cur)); cur = []
        cur.append(ln)
        if sum(map(len, cur)) > PART_CHARS:
            parts.append("\n".join(cur)); cur = []
    if cur:
        parts.append("\n".join(cur))
    return parts


def claude(prompt: str, stdin: str) -> str:
    r = subprocess.run(["claude", "-p", prompt, "--model", MODEL], input=stdin,
                       capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"claude rc={r.returncode}: {(r.stderr or '')[:300]}")
    return r.stdout.strip()


def parse_json_array(raw: str) -> list[dict]:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    s, e = raw.find("["), raw.rfind("]")
    if s < 0 or e < 0:
        raise RuntimeError("JSON 배열 없음")
    return json.loads(raw[s:e + 1])


def once(text: str, sub: str) -> bool:
    return bool(sub) and text.count(sub) == 1


def sanitize(items: list[dict]) -> tuple[list[dict], dict]:
    """음영 규칙 검증: kw/kwr 은 q 안에서, kw2 는 exp 안에서 유일해야 함. 아니면 음영만 제거. q/ans 없으면 버림."""
    out, stat = [], {"drop": 0, "kw_strip": 0}
    for it in items:
        q, ans = (it.get("q") or "").strip(), (it.get("ans") or "").strip().upper()
        if not q or ans not in ("O", "X"):
            stat["drop"] += 1; continue
        exp = (it.get("exp") or "").strip()
        item = {"q": q, "ans": ans, "exp": exp, "grade": it.get("grade") or "-", "theme": (it.get("theme") or "").strip()}
        if ans == "O":
            if once(q, it.get("kw", "")):
                item["kw"] = it["kw"]
            elif it.get("kw"):
                stat["kw_strip"] += 1
        else:
            if once(q, it.get("kwr", "")):
                item["kwr"] = it["kwr"]
            elif it.get("kwr"):
                stat["kw_strip"] += 1
            if once(exp, it.get("kw2", "")):
                item["kw2"] = it["kw2"]
            elif it.get("kw2"):
                stat["kw_strip"] += 1
        if item["grade"] not in ("위", "장", "-"):
            item["grade"] = "-"
        out.append(item)
    return out, stat


def gen_questions(part: str, idx: int, total: int, chid: str) -> list[dict]:
    n = max(MIN_Q_PER_PART, hangul(part) // Q_PER_HANGUL)
    for attempt in range(1, 3):
        try:
            log(f"{chid} 문제 생성 {idx + 1}/{total} (목표 {n}문항, 한글 {hangul(part)}자, {attempt}회차)")
            items, stat = sanitize(parse_json_array(claude(Q_PROMPT.format(n=n), part)))
            if len(items) < n * 0.5:
                raise RuntimeError(f"문항 {len(items)}개 — 목표의 절반 미만")
            log(f"  → {len(items)}문항 (X {sum(1 for i in items if i['ans']=='X')}, 폐기 {stat['drop']}, 음영제거 {stat['kw_strip']})")
            return items
        except (RuntimeError, json.JSONDecodeError) as e:
            log(f"  실패: {str(e)[:200]}")
    raise RuntimeError(f"{chid} part {idx} 문제 생성 2회 실패")


def gen_intro(parts: list[str], src: str, chid: str) -> str:
    htmls = []
    for i, p in enumerate(parts):
        for attempt in range(1, 3):
            try:
                log(f"{chid} 핵심요약 복원 {i + 1}/{len(parts)} ({attempt}회차)")
                htmls.append(intro_convert(p)); break
            except RuntimeError as e:
                log(f"  실패: {e}")
                if attempt == 2:
                    raise
    html = "\n".join(htmls)
    intro_validate(src, html)
    return html


def build(chid: str, dry: bool) -> None:
    meta = next(c for c in CHAPTERS if c[0] == chid)
    _, title, start, end = meta
    src = chapter_src(start, end)
    parts = split_parts(src)
    WORK.mkdir(exist_ok=True)
    (WORK / f"{chid}.src.txt").write_text(src, encoding="utf-8")
    log(f"{chid} {title}: {len(src)}자, 한글 {hangul(src)}자, {len(parts)}부분")
    if dry:
        return
    questions = []
    for i, p in enumerate(parts):
        questions += gen_questions(p, i, len(parts), chid)
    num = int(chid[2:])
    for i, q in enumerate(questions, 1):
        q["no"] = f"{i:02d}"
        q["src"] = f"{SRC_LABEL} 제{num}장"
    chap = {"title": f"위험물{num:02d} {title}", "questions": questions}
    (BASE / "public" / "chapters" / f"{chid}.json").write_text(json.dumps(chap, ensure_ascii=False, indent=1), encoding="utf-8")
    html = gen_intro(parts, src, chid)
    (BASE / "public" / "intros" / f"{chid}.json").write_text(json.dumps({"intro": html, "introEasy": ""}, ensure_ascii=False, indent=1), encoding="utf-8")
    update_manifest(BASE, chid, chap["title"], questions)
    update_qgrades(BASE, chid, questions)
    log(f"DONE {chid} {title}: {len(questions)}문항 (X {sum(1 for q in questions if q['ans']=='X')}), 요약 {len(html)}자")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    chid, dry = sys.argv[1].lower(), "--dry" in sys.argv
    if chid not in {c[0] for c in CHAPTERS}:
        sys.exit(f"ERROR: 알 수 없는 챕터 {chid}")
    build(chid, dry)


if __name__ == "__main__":
    main()
