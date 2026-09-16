#!/usr/bin/env python3
"""묶음별 작성으로 생긴 요약 중복을 통합·압축 (내용 손실 없이).

- 유사 섹션 병합, 같은 판례·쟁점 반복 제거. 두문자·판례 결론·수치는 전부 유지.
- 압축 후 커버리지 검사, 미커버 발생 시 1회 보강.

사용: python3 intro_compress.py ch53
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intro_audit as ia  # noqa: E402

TARGET_RATIO = 0.7         # 목표: 30% 압축

COMPRESS_PROMPT = """당신은 유휘운 행정법 강사입니다. 아래 챕터 핵심요약은 여러 파트를 이어 붙여 만들어져 **중복**이 있습니다.
중복을 통합해 전체 분량을 약 {pct}% 수준으로 압축하되, 정보는 하나도 잃지 마세요.

규칙:
1. 같은 쟁점·판례를 다룬 섹션은 하나로 병합하세요. 판례 결론, 요건, 주체, 숫자, 예외, <b>강조, <div class="tip">의 두문자·함정은 전부 유지.
2. 삭제 대상은 오직 "중복 서술"뿐입니다. 한 번만 나오는 내용은 문장을 줄여 쓸 수는 있어도 사실 자체를 빼면 안 됩니다.
3. 섹션 순서는 논리적 흐름(비교·대조 구도)에 맞게 재배열해도 됩니다.
4. 허용 태그: h4, p, ul, li, b, div class="tip". 파일을 읽거나 수정하지 말 것.
5. 출력 형식 (구분 표식 4개 필수):
<<<INTRO>>>
(압축된 상세 요약 HTML)
<<<END_INTRO>>>
<<<EASY>>>
(압축된 쉬운말 요약 HTML — 같은 원칙, ~해요체)
<<<END_EASY>>>

[현재 상세 요약]
{intro}

[현재 쉬운말 요약]
{easy}
"""


def run(ch: str) -> None:
    intro_path = ia.INTRO_DIR / f"{ch}.json"
    intro = json.load(open(intro_path))
    qs = [q for q in json.load(open(ia.CHAP_DIR / f"{ch}.json"))["questions"]
          if q.get("grade") not in ia.SKIP_GRADES]
    shutil.copy(intro_path, f"/tmp/intro_precompress_{ch}.json")
    before = len(intro["intro"])

    out = ia.parse_marked(ia.claude(COMPRESS_PROMPT.format(
        pct=int(TARGET_RATIO * 100), intro=intro["intro"], easy=intro.get("introEasy", ""))))
    if len(out["intro"]) < before * 0.4 or "<h4" not in out["intro"]:
        raise ValueError(f"압축 결과 이상 (상세 {len(out['intro'])}자)")
    cand = {**intro, "intro": out["intro"], "introEasy": out["introEasy"]}

    missing = ia.audit(cand["intro"], qs)
    print(f"{ch}: 압축 {before}→{len(cand['intro'])}자, 미커버 {len(missing)}건", flush=True)
    if missing:                                 # 압축으로 빠진 것만 1회 복구
        try:
            ocr = ia.load_ocr()
        except Exception:
            ocr = ""
        cand = ia.patch(cand, qs, missing, ocr)
        missing = ia.audit(cand["intro"], qs)
        print(f"{ch}: 복구 후 미커버 {len(missing)}건 ({len(cand['intro'])}자)", flush=True)

    intro_path.write_text(json.dumps(cand, ensure_ascii=False, indent=1))
    if "map" in cand:
        from intro_map import build
        build(ch)
    print(f"{ch}: 압축 완료 — 상세 {len(cand['intro'])}자, 쉬운말 {len(cand['introEasy'])}자, "
          f"미커버 {len(missing)}건", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run(sys.argv[1])
