#!/usr/bin/env python3
"""교재(소방공무원법) '단원별 핵심요약' OCR 텍스트 → 앱 핵심요약(intro) HTML로 교체.

원문 그대로 옮기는 것이 목적(사용자 지시 2026-09-13). claude 는 표 구조 복원·OCR 노이즈 제거만 하고
요약·의역·추가는 금지. 결과는 한글 글자 수로 원문 대비 누락을 검증한 뒤 intros/<ch>.json 의 intro 만 교체한다.

사용법: python3 fs_summary_html.py fs01 [/tmp/fs_sum/fs01.txt]
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
INTRO_DIR = BASE / "public" / "intros"
SRC_DIR = Path("/tmp/fs_sum")
MODEL = "opus"
MIN_KEEP_RATIO = 0.85          # 변환 후 한글 글자 수 / 원문 한글 글자 수 — 이보다 낮으면 누락으로 판정
MIN_SECTIONS = 3
CLAUDE_TIMEOUT = 900

PROMPT = """아래는 소방승진 교재 '단원별 핵심요약' 표를 OCR 한 텍스트입니다.
원래 표는 왼쪽 열에 주제 라벨(예: 법의 목적, 소방공무원 법령의 용어, 계급 구분, 임용권자 …), 오른쪽 열에 내용이 있는데
OCR 과정에서 라벨이 본문 줄 중간에 끼어 들어가 있고, 페이지 머리말·꼬리말 노이즈가 섞여 있습니다.
이것을 학습 앱 핵심요약 HTML 로 복원하세요.

[절대 규칙 — 원문 그대로]
- 내용은 교재 문장 그대로 옮깁니다. 요약·의역·문장 추가·삭제·순서 변경 금지. 한 항목도 빠뜨리지 마세요.
- 줄바꿈으로 끊긴 한 문장은 이어 붙입니다. 번호 체계 1) ① ㉠ 는 원문 순서대로 유지합니다.
- 고칠 수 있는 것은 OCR 오류만: 오타(죄하급→최하급, 소방공무워→소방공무원), 기호(© ⑪ ⓐ 등 → ㉠㉡㉢ 순서, • → ·, （）→ ()),
  페이지 노이즈("CHAPTER 01 총칙 27", "Chapter 0 1", "28 PART O 소방공무원법", "a a l d v H O" 같은 줄) 제거.
- 라벨 텍스트가 본문 줄 안에 끼어 있으면(예: "소방공무원 ③ 소멸 : …" / "법령의 용어 2) …") 라벨만 빼내어 제목으로 쓰고 본문은 그대로 둡니다.

[HTML 형식]
- 주제 라벨마다 <h4>📌 라벨</h4> 를 쓰고 그 아래 내용을 <ul><li> 로 씁니다. 번호가 있는 항목은 번호를 텍스트로 남기고 하위 항목은 <ul> 중첩.
- 표 안의 단문(예: 법의 목적)은 <p> 하나로.
- 시험에서 바꿔 내는 숫자·기간·주체·요건 어구만 <b> 로 감쌉니다(항목당 1~3개, 문장 전체 볼드 금지).
- 출력은 HTML 만. 설명·주석·코드펜스 금지. 첫 글자는 반드시 <h4 로 시작.
"""


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def hangul_count(text: str) -> int:
    return len(re.findall(r"[가-힣]", text))


def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def strip_page_noise(src: str) -> str:
    """검증용: 원문에서 페이지 머리말·꼬리말 줄을 제거한 한글 기준선."""
    keep = []
    for ln in src.splitlines():
        if re.search(r"CHAPTER \d\d|^Chapter\b|PART O|핵심요약$", ln):
            continue
        keep.append(ln)
    return "\n".join(keep)


def convert(src: str) -> str:
    r = subprocess.run(["claude", "-p", PROMPT, "--model", MODEL], input=src,
                       capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"claude rc={r.returncode}: {(r.stderr or '')[:300]}")
    html = r.stdout.strip()
    html = re.sub(r"^```(?:html)?\s*|\s*```$", "", html).strip()
    start = html.find("<h4")
    if start < 0:
        raise RuntimeError("출력에 <h4> 없음")
    return html[start:]


def validate(src: str, html: str) -> None:
    base, got = hangul_count(strip_page_noise(src)), hangul_count(strip_tags(html))
    ratio = got / max(base, 1)
    n_sec = len(re.findall(r"<h4", html))
    log(f"검증: 한글 {got}/{base}자 ({ratio:.0%}), 섹션 {n_sec}개")
    if ratio < MIN_KEEP_RATIO:
        raise RuntimeError(f"누락 의심: 한글 보존율 {ratio:.0%} < {MIN_KEEP_RATIO:.0%}")
    if n_sec < MIN_SECTIONS:
        raise RuntimeError(f"섹션 {n_sec}개 — 너무 적음")


def replace_intro(ch: str, html: str) -> None:
    path = INTRO_DIR / f"{ch}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    backup = SRC_DIR / f"{ch}.intro.bak.json"
    if not backup.exists():
        backup.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    new_data = {**data, "intro": html}
    path.write_text(json.dumps(new_data, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"교체 완료: {path} (백업 {backup})")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ch = sys.argv[1].lower()
    src_path = Path(sys.argv[2]) if len(sys.argv) > 2 else SRC_DIR / f"{ch}.txt"
    src = src_path.read_text(encoding="utf-8")
    log(f"{ch}: 원문 {len(src)}자 → claude {MODEL} 변환")
    for attempt in range(1, 3):
        try:
            html = convert(src)
            validate(src, html)
            break
        except RuntimeError as e:
            log(f"{attempt}회차 실패: {e}")
            if attempt == 2:
                sys.exit(f"ERROR: {ch} 변환 2회 실패")
    (SRC_DIR / f"{ch}.intro.html").write_text(html, encoding="utf-8")
    replace_intro(ch, html)


if __name__ == "__main__":
    main()
