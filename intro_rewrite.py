#!/usr/bin/env python3
"""챕터 핵심요약을 유휘운 요약강의 기반으로 처음부터 새로 작성.

근거 우선순위: ① 유휘운 요약강의 전사(두문자·암기법 포함) ② 교재 OCR 원문 ③ 문항 해설.
문항(Z·무등급 제외)을 묶음으로 나눠 묶음마다 강의·원문 발췌를 근거로 상세·쉬운말
요약 섹션을 새로 쓰고 조립한다. 마지막에 커버리지 검사 1회 + 부족분 마무리 1회.

사용: python3 intro_rewrite.py ch53
사전조건: /tmp/ch53_lecture.txt(강의 전사), /tmp/ch53_mnemonics.txt(두문자 모음)
결과: intros/chNN.json 교체 (기존본은 /tmp/intro_backup_chNN.json)
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intro_audit as ia  # claude 호출·발췌·검사·마커 파싱 재사용  # noqa: E402

CHUNK = 20                 # 묶음당 문항 수
LECTURE_PER_Q = 1000       # 강의 전사는 말이 길어 발췌 폭을 넓게

WRITE_PROMPT = """당신은 유휘운 행정법 강사 본인입니다. 챕터 "{title}"의 핵심요약 중 아래 문항 묶음을 담당하는 부분을 **새로** 작성하세요.
목표: 이 요약만 읽은 학생이 [문항] 각각의 정답을 확신을 갖고 고를 수 있어야 합니다.

근거 우선순위 (창작 금지):
① [강의 전사 발췌] — 유휘운 요약강의 실제 설명. 강의의 논리 전개·비교 구도·강조 포인트를 그대로 살리세요. 자동자막이라 오타가 있으니 문맥으로 교정해서 쓰세요.
② [두문자 모음] — 관련 두문자·암기법이 있으면 반드시 요약에 그대로 넣으세요.
③ [교재 원문 발췌] — 강의에 없는 세부 쟁점 보충.
④ 위에 없는 내용만 문항의 해설 문구를 그대로 사용.

작성 규칙:
1. 문항마다 정답을 가르는 구체적 사실(판례 결론·요건·주체·숫자·예외)을 명시하고 핵심어는 <b>로 감싸세요. 객관식은 오답 선지 판별 근거까지 포함.
2. 강의처럼 "처분 O vs 처분 X" 같은 비교·대조 구도로 <h4> 섹션 + <ul><li>로 정리하세요. 어떤 문항도 빠뜨리지 마세요. 문항 번호는 본문에 쓰지 마세요.
3. 함정·헷갈림 포인트와 두문자는 <div class="tip">에 정리하세요.
4. "상세"는 정확한 법률 용어, "쉬운말"은 같은 내용을 강의 말투처럼 쉬운 구어체(~해요/~예요)로. 둘 다 모든 문항을 커버해야 합니다.
5. 허용 태그: h4, p, ul, li, b, div class="tip". 그 외 태그·마크다운 금지. 파일을 읽거나 수정하지 말 것.
6. 출력은 아래 형식 그대로 (구분 표식 4개 필수):
<<<INTRO>>>
(상세 요약 HTML)
<<<END_INTRO>>>
<<<EASY>>>
(쉬운말 요약 HTML)
<<<END_EASY>>>

[문항] (번호, 정답, 지문, 해설)
{qblock}

[강의 전사 발췌] (유휘운 요약강의 자동자막)
{lecture}

[두문자 모음] (유휘운 두문자 영상 전사)
{mnemonics}

[교재 원문 발췌] (유휘운 행정법 OCR)
{excerpts}
"""


def write_chunk(title: str, qs: list[dict], lecture: str, mnemonics: str, ocr: str) -> dict:
    prompt = WRITE_PROMPT.format(
        title=title, qblock=ia.q_block(qs),
        lecture=ia.ocr_excerpts(lecture, qs, per=LECTURE_PER_Q) or "(해당 발췌 없음)",
        mnemonics=mnemonics or "(없음)",
        excerpts=ia.ocr_excerpts(ocr, qs) or "(발췌 없음)")
    out = ia.parse_marked(ia.claude(prompt))
    if "<h4" not in out["intro"] or len(out["intro"]) < 300 or len(out["introEasy"]) < 300:
        raise ValueError(f"묶음 결과 형식 이상 (상세 {len(out['intro'])}자)")
    return out


def deploy() -> None:
    r = subprocess.run(["npx", "firebase-tools", "deploy", "--only", "hosting",
                        "--project", "work-schedule-dash-4ceb2"],
                       cwd=str(ia.ROOT), capture_output=True, text=True, timeout=300)
    print("배포 " + ("OK" if r.returncode == 0 else f"실패: {r.stderr[-200:]}"), flush=True)


def run(ch: str) -> None:
    chap = json.load(open(ia.CHAP_DIR / f"{ch}.json"))
    qs = [q for q in chap["questions"] if q.get("grade") not in ia.SKIP_GRADES]
    intro_path = ia.INTRO_DIR / f"{ch}.json"
    old = json.load(open(intro_path))
    shutil.copy(intro_path, f"/tmp/intro_backup_{ch}.json")

    lecture = Path(f"/tmp/{ch}_lecture.txt").read_text(errors="ignore")
    mnemonics = Path(f"/tmp/{ch}_mnemonics.txt").read_text(errors="ignore")
    ocr = ia.load_ocr()

    chunks = [qs[i:i + CHUNK] for i in range(0, len(qs), CHUNK)]
    print(f"{ch}: {len(qs)}문항 → {len(chunks)}묶음, 강의 {len(lecture)}자 기반 새로 작성", flush=True)
    details, easies = [], []
    for n, chunk in enumerate(chunks, 1):
        out = write_chunk(chap["title"], chunk, lecture, mnemonics, ocr)
        details.append(out["intro"])
        easies.append(out["introEasy"])
        print(f"{ch}: 묶음 {n}/{len(chunks)} 완료 (상세 {len(out['intro'])}자)", flush=True)
        note = (f'<p><b>⚠️ 요약 새로 작성 중 ({n}/{len(chunks)} 묶음 완료)</b></p>'
                if n < len(chunks) else "")
        partial = {**old, "intro": note + "\n".join(details),
                   "introEasy": note + "\n".join(easies)}
        intro_path.write_text(json.dumps(partial, ensure_ascii=False, indent=1))
        deploy()                                # 진행분 즉시 배포 (사용자 지시 2026-09-15)

    intro = {**old, "intro": "\n".join(details), "introEasy": "\n".join(easies)}

    missing = ia.audit(intro["intro"], qs)
    print(f"{ch}: 새 요약 검사 — 미커버 {len(missing)}건", flush=True)
    if missing:                                 # 부족분만 1회 마무리
        intro = ia.patch(intro, qs, missing, ocr)
        missing = ia.audit(intro["intro"], qs)
        print(f"{ch}: 마무리 후 미커버 {len(missing)}건", flush=True)

    intro_path.write_text(json.dumps(intro, ensure_ascii=False, indent=1))
    if "map" in intro:
        from intro_map import build
        build(ch)
    deploy()
    print(f"{ch}: 완료 — 상세 {len(intro['intro'])}자, 쉬운말 {len(intro['introEasy'])}자, "
          f"최종 미커버 {len(missing)}건", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run(sys.argv[1])
