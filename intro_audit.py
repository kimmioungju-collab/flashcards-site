#!/usr/bin/env python3
"""챕터 핵심요약(intros/chNN.json) 전수 검사·보강.

1) 검사: 요약만 읽은 학생이 각 문항을 풀 수 있는지 claude가 문항별 판정 → 누락 목록
2) 보강: 누락 문항의 핵심 포인트를 상세(intro)·쉬운말(introEasy) 요약에 삽입
3) 재검증: 보강 후 다시 검사. 누락 0이 될 때까지 최대 MAX_ROUNDS회

사용: python3 intro_audit.py ch52 [--check-only]
결과: /tmp/intro_audit/chNN.json (누락 목록·라운드별 결과), intros/chNN.json 갱신
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHAP_DIR = ROOT / "public" / "chapters"
INTRO_DIR = ROOT / "public" / "intros"
OUT_DIR = Path("/tmp/intro_audit")
CLAUDE = "/opt/homebrew/bin/claude"
MODEL = "claude-fable-5-1"  # Fable 5.1 전환 (사용자 지시 2026-09-18)
MAX_ROUNDS = 6             # 미커버 0까지 (사용자 지시 2026-09-15: Z·무등급 제외 전 문항 커버)
SKIP_GRADES = {"-", "Z"}   # 등급없음(무)·Z는 요약 커버 대상 아님 (사용자 지시 2026-09-14)
TIMEOUT = 900

# 보강 문장 근거는 교재 OCR 원문에서 (사용자 지시 2026-09-15)
OCR_PDF = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/행정법 유휘운_ocr.pdf"
OCR_TXT = Path("/tmp/행정법_ocr.txt")
OCR_PER_Q = 700            # 문항당 원문 발췌 최대 길이
OCR_TOTAL = 15000          # patch 1회당 발췌 총량 상한
CASE_RE = re.compile(r"\d{2,4}\s?[가-힣]{1,3}\s?\d{2,6}")   # 판례번호 (예: 93누7365)


def load_ocr() -> str:
    if not OCR_TXT.exists() or OCR_TXT.stat().st_size < 1000:
        subprocess.run(["pdftotext", "-raw", str(OCR_PDF), str(OCR_TXT)], check=True, timeout=3600)
    return OCR_TXT.read_text(errors="ignore")


def ocr_excerpts(ocr: str, miss_qs: list[dict], per: int = OCR_PER_Q, total_cap: int = OCR_TOTAL) -> str:
    """문항별로 판례번호·핵심어를 앵커로 원문(교재 OCR·강의 전사 등) 주변부를 발췌."""
    parts: list[str] = []
    used: set[int] = set()
    total = 0
    for q in miss_qs:
        src = f"{q.get('exp', '')} {q['q']}"
        anchors = CASE_RE.findall(src)
        anchors += sorted(set(re.findall(r"[가-힣]{4,}", src)), key=len, reverse=True)[:4]
        for a in anchors:
            i = ocr.find(a)
            if i < 0:
                i = ocr.find(a.replace(" ", ""))
            if i < 0:
                continue
            if i // 1500 in used:               # 같은 구역 중복 발췌 방지
                break
            used.add(i // 1500)
            chunk = re.sub(r"\s+", " ", ocr[max(0, i - 200):i + per]).strip()
            parts.append(f"[{q['no']}번 관련] …{chunk}…")
            total += len(chunk)
            break                               # 문항당 발췌 1개
        if total > total_cap:
            break
    return "\n".join(parts)


def claude(prompt: str) -> str:
    # 도구 전부 차단 + 작업폴더 /tmp: claude가 요약 파일을 직접 고치려 들지 않고 JSON만 출력하게 함
    r = subprocess.run([CLAUDE, "-p", "--model", MODEL, "--output-format", "text", "--tools", ""],
                       input=prompt, capture_output=True, text=True, timeout=TIMEOUT, cwd="/tmp")
    if r.returncode != 0:
        # SessionEnd 훅은 응답이 다 나온 뒤 죽으면서 종료코드만 더럽힌다 → 본문이 있으면 정상 취급
        if r.stdout and r.stdout.strip():
            return r.stdout
        raise RuntimeError(f"claude 실패: {(r.stderr or r.stdout)[-300:]}")
    return r.stdout


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSON 없음: " + text[:200])
    return json.loads(m.group(0), strict=False)   # 문자열 안 원시 줄바꿈·탭 허용


def parse_marked(text: str) -> dict:
    """<<<INTRO>>>…<<<END_INTRO>>> / <<<EASY>>>…<<<END_EASY>>> 형식 파싱 (JSON 이스케이프 문제 회피)."""
    a = re.search(r"<<<INTRO>>>\s*(.*?)\s*<<<END_INTRO>>>", text, re.S)
    b = re.search(r"<<<EASY>>>\s*(.*?)\s*<<<END_EASY>>>", text, re.S)
    if not a or not b:
        raise ValueError("구분 표식 없음: " + text[:200])
    return {"intro": a.group(1), "introEasy": b.group(1)}


def strip_html(h: str) -> str:
    return re.sub(r"<[^>]+>", "", h)


def q_block(qs: list[dict]) -> str:
    lines = []
    for q in qs:
        lines.append(f"[{q['no']}] ({q.get('ans','')}) {q['q']}\n   해설: {q.get('exp','')}")
    return "\n".join(lines)


def audit(intro_html: str, qs: list[dict]) -> list[dict]:
    """요약만으로 못 푸는 문항 번호 + 필요한 핵심 포인트."""
    prompt = f"""당신은 행정법 수험 교재 검수자입니다.
아래 [요약]만 읽은 학생이 [문제] 각각의 정답을 확신을 갖고 고를 수 있는지 문항별로 엄격히 판정하세요.
- 판정 기준: 정답을 가르는 구체적 사실·판례 결론·요건·숫자·주체가 요약에 명시돼 있어야 "가능". 일반론에서 추론만 가능하면 "불가능".
- 객관식(①~⑤)은 정답 선지뿐 아니라 오답 선지 판별에 필요한 내용도 요약에 있어야 "가능".
- 파일을 읽거나 수정하지 말고, 출력은 JSON 하나만: {{"missing":[{{"no":"문항번호","point":"요약에 추가해야 할 핵심 포인트 한 문장(정답 방향 포함)"}}]}}
- 모두 가능하면 {{"missing":[]}}

[요약]
{strip_html(intro_html)}

[문제] (번호, 정답, 지문, 해설)
{q_block(qs)}
"""
    return parse_json(claude(prompt)).get("missing", [])


def patch(intro: dict, qs: list[dict], missing: list[dict], ocr: str = "") -> dict:
    """누락 문항 포인트를 상세·쉬운말 요약에 삽입한 새 intro 반환."""
    miss_nos = {m["no"] for m in missing}
    miss_qs = [q for q in qs if q["no"] in miss_nos]
    points = "\n".join(f"- [{m['no']}] {m['point']}" for m in missing)
    excerpts = ocr_excerpts(ocr, miss_qs) if ocr else ""
    ocr_rule = ""
    ocr_block = ""
    if excerpts:
        ocr_rule = ("0. 보강 문장은 반드시 [교재 원문 발췌]의 표현·결론을 근거로 작성하세요. "
                    "발췌에 해당 내용이 없으면 해당 문항의 해설 문구를 그대로 옮기세요. "
                    "원문·해설에 없는 내용을 새로 지어내는 것 금지.\n")
        ocr_block = f"\n[교재 원문 발췌] (유휘운 행정법 OCR)\n{excerpts}\n"
    prompt = f"""당신은 행정법 1타 강사입니다. 아래 챕터 핵심요약이 일부 기출문항을 커버하지 못합니다.
[누락 문항]의 정답을 가르는 핵심 내용을 요약에 **빠짐없이** 추가하세요.

규칙:
{ocr_rule}1. 기존 요약의 내용·구조·문장은 유지하고, 관련 섹션의 <ul>에 <li>를 추가하거나 적절한 위치에 새 <h4> 섹션+<ul>을 추가하세요. 기존 내용 삭제·축약 금지.
   단, 기존 요약 문장이 [누락 문항]의 정답·해설과 **모순**되면 해설(교재 원문)이 맞습니다 — 그 문장(관련 <li>·함정 항목 전부)을 해설에 맞게 고치세요. 모순 문장을 남겨두면 학생이 반대로 외웁니다.
2. 누락 문항 하나하나가 요약만 읽고 풀리도록 구체적으로 쓰세요(판례 결론, 요건, 주체, 숫자, 예외). 정답을 가르는 핵심어는 <b>로 감싸세요.
3. 객관식은 오답 선지까지 판별되도록 관련 내용을 모두 넣으세요.
4. "상세"는 정확한 법률 용어로, "쉬운말"은 초보자용 구어체(~해요/~예요)로 같은 내용을 쉽게 풀어 씁니다. 둘 다 누락 문항을 전부 커버해야 합니다.
5. 허용 태그: h4, p, ul, li, b, div class="tip". 그 외 태그·마크다운 금지.
6. 파일을 읽거나 수정하지 말고, JSON·마크다운 없이 아래 형식 그대로 출력하세요(구분 표식 4개 필수):
<<<INTRO>>>
(상세 요약 HTML 전체)
<<<END_INTRO>>>
<<<EASY>>>
(쉬운말 요약 HTML 전체)
<<<END_EASY>>>

[누락 문항 포인트]
{points}

[누락 문항 원문] (번호, 정답, 지문, 해설)
{q_block(miss_qs)}
{ocr_block}

[현재 상세 요약]
{intro['intro']}

[현재 쉬운말 요약]
{intro.get('introEasy','')}
"""
    out = parse_marked(claude(prompt))
    if len(out.get("intro", "")) < len(intro["intro"]) * 0.9 or "<h4" not in out.get("intro", ""):
        raise ValueError("보강 결과가 기존보다 짧거나 형식 이상")
    if len(out.get("introEasy", "")) < len(intro.get("introEasy", "")) * 0.9:
        raise ValueError("쉬운말 보강 결과가 기존보다 짧음")
    return {**intro, "intro": out["intro"], "introEasy": out["introEasy"]}   # map 등 부가 키 보존


def run(ch: str, check_only: bool = False) -> dict:
    qs = [q for q in json.load(open(CHAP_DIR / f"{ch}.json"))["questions"] if q.get("grade") not in SKIP_GRADES]
    intro_path = INTRO_DIR / f"{ch}.json"
    intro = json.load(open(intro_path))
    OUT_DIR.mkdir(exist_ok=True)
    report = {"ch": ch, "questions": len(qs), "rounds": []}

    missing = audit(intro["intro"], qs)
    report["rounds"].append({"missing": missing})
    print(f"{ch}: {len(qs)}문항 중 요약 미커버 {len(missing)}건", flush=True)

    if check_only or not missing:
        report["final_missing"] = len(missing)
        (OUT_DIR / f"{ch}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
        return report

    try:
        ocr = load_ocr()
    except Exception as e:                      # OCR 준비 실패 시 명시하고 해설 기반으로 진행
        print(f"{ch}: OCR 로드 실패({e}) — 해설 기반으로 보강", flush=True)
        ocr = ""

    for rnd in range(MAX_ROUNDS):
        intro = patch(intro, qs, missing, ocr)
        missing = audit(intro["intro"], qs)
        report["rounds"].append({"missing": missing})
        print(f"{ch}: 보강 {rnd+1}회차 후 미커버 {len(missing)}건 (상세 {len(intro['intro'])}자)", flush=True)
        if not missing:
            break

    intro_path.write_text(json.dumps(intro, ensure_ascii=False, indent=1))
    if "map" in intro:                       # 요약 본문이 바뀌면 문항→요약 매핑도 갱신
        from intro_map import build          # (지연 import: intro_map이 이 모듈을 import함)
        build(ch)
    report["final_missing"] = len(missing)
    report["final_len"] = len(intro["intro"])
    (OUT_DIR / f"{ch}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run(sys.argv[1], "--check-only" in sys.argv)
