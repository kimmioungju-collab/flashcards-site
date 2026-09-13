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
MODEL = "sonnet"
MAX_ROUNDS = 3
TIMEOUT = 900


def claude(prompt: str) -> str:
    r = subprocess.run([CLAUDE, "-p", "--model", MODEL, "--output-format", "text"],
                       input=prompt, capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"claude 실패: {(r.stderr or r.stdout)[-300:]}")
    return r.stdout


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSON 없음: " + text[:200])
    return json.loads(m.group(0))


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
- 출력은 JSON 하나만: {{"missing":[{{"no":"문항번호","point":"요약에 추가해야 할 핵심 포인트 한 문장(정답 방향 포함)"}}]}}
- 모두 가능하면 {{"missing":[]}}

[요약]
{strip_html(intro_html)}

[문제] (번호, 정답, 지문, 해설)
{q_block(qs)}
"""
    return parse_json(claude(prompt)).get("missing", [])


def patch(intro: dict, qs: list[dict], missing: list[dict]) -> dict:
    """누락 문항 포인트를 상세·쉬운말 요약에 삽입한 새 intro 반환."""
    miss_nos = {m["no"] for m in missing}
    miss_qs = [q for q in qs if q["no"] in miss_nos]
    points = "\n".join(f"- [{m['no']}] {m['point']}" for m in missing)
    prompt = f"""당신은 행정법 1타 강사입니다. 아래 챕터 핵심요약이 일부 기출문항을 커버하지 못합니다.
[누락 문항]의 정답을 가르는 핵심 내용을 요약에 **빠짐없이** 추가하세요.

규칙:
1. 기존 요약의 내용·구조·문장은 유지하고, 관련 섹션의 <ul>에 <li>를 추가하거나 적절한 위치에 새 <h4> 섹션+<ul>을 추가하세요. 기존 내용 삭제·축약 금지.
2. 누락 문항 하나하나가 요약만 읽고 풀리도록 구체적으로 쓰세요(판례 결론, 요건, 주체, 숫자, 예외). 정답을 가르는 핵심어는 <b>로 감싸세요.
3. 객관식은 오답 선지까지 판별되도록 관련 내용을 모두 넣으세요.
4. "상세"는 정확한 법률 용어로, "쉬운말"은 초보자용 구어체(~해요/~예요)로 같은 내용을 쉽게 풀어 씁니다. 둘 다 누락 문항을 전부 커버해야 합니다.
5. 허용 태그: h4, p, ul, li, b, div class="tip". 그 외 태그·마크다운 금지.
6. 출력은 JSON 하나만: {{"intro":"<상세 요약 HTML 전체>","introEasy":"<쉬운말 요약 HTML 전체>"}}  (문자열 내부 줄바꿈은 \\n으로 이스케이프)

[누락 문항 포인트]
{points}

[누락 문항 원문] (번호, 정답, 지문, 해설)
{q_block(miss_qs)}

[현재 상세 요약]
{intro['intro']}

[현재 쉬운말 요약]
{intro.get('introEasy','')}
"""
    out = parse_json(claude(prompt))
    if len(out.get("intro", "")) < len(intro["intro"]) * 0.9 or "<h4" not in out.get("intro", ""):
        raise ValueError("보강 결과가 기존보다 짧거나 형식 이상")
    if len(out.get("introEasy", "")) < len(intro.get("introEasy", "")) * 0.9:
        raise ValueError("쉬운말 보강 결과가 기존보다 짧음")
    return {"intro": out["intro"], "introEasy": out["introEasy"]}


def run(ch: str, check_only: bool = False) -> dict:
    qs = json.load(open(CHAP_DIR / f"{ch}.json"))["questions"]
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

    for rnd in range(MAX_ROUNDS):
        intro = patch(intro, qs, missing)
        missing = audit(intro["intro"], qs)
        report["rounds"].append({"missing": missing})
        print(f"{ch}: 보강 {rnd+1}회차 후 미커버 {len(missing)}건 (상세 {len(intro['intro'])}자)", flush=True)
        if not missing:
            break

    intro_path.write_text(json.dumps(intro, ensure_ascii=False, indent=1))
    report["final_missing"] = len(missing)
    report["final_len"] = len(intro["intro"])
    (OUT_DIR / f"{ch}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run(sys.argv[1], "--check-only" in sys.argv)
