"""교재 본문 위원회 낭독 대본(public/audio/committees_book_script.txt) 파서.
화면용 원문과 낭독문을 한 원본에서 만든다 (대본 표기 규칙은 대본 파일 머리말 참고).

  python3 committees_book.py            # 트랙별 분량 요약
  python3 committees_book.py --speech   # 낭독문 전체 출력 (검수용)
  python3 committees_book.py --verify   # 교재 OCR(/tmp/fs_ocr_raw.txt)과 글자 대조
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SCRIPT = BASE / "public" / "audio" / "committees_book_script.txt"
OCR_TXT = Path("/tmp/fs_ocr_raw.txt")           # pdftotext -raw 소방공무원법_ocr.pdf
PAGE_OFFSET = 2                                  # PDF 쪽 = 교재 인쇄 쪽 + 2
VERIFY_GRAM = 6                                  # 대조용 글자 조각 길이
VERIFY_MIN = 0.85                                # 이 미만이면 사람이 다시 볼 줄로 보고

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
SUB_MARKS = "㉠㉡㉢㉣㉤㉥㉦㉧㉨㉩"
THIRD_MARKS = "ⓐⓑⓒⓓⓔⓕ"
ORD_1 = ["첫째", "둘째", "셋째", "넷째", "다섯째", "여섯째", "일곱째", "여덟째", "아홉째", "열째"]
ORD_2 = ["하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉", "열"]
ORD_3 = ["가", "나", "다", "라", "마", "바"]

SCREEN_ONLY = re.compile(r"〔[^〕]*〕")           # 화면에만 보이는 부분
OVERRIDE = re.compile(r"\{([^|{}]*)\|([^{}]*)\}")  # {화면|낭독}
PAREN = re.compile(r"\s*\(([^()]*)\)([가-힣]*)")
DATE = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?")
SENTENCE_END = ("다", "제외")                      # 괄호 속이 문장이면 조사를 앞으로 당겨 읽는다
SIDO = re.compile(r"(?<![가-힣])시·도(?!·)")       # 특별자치시·도·특별자치도 의 "시·도"는 건드리지 않는다
DOT_RUN = re.compile(r"[가-힣0-9]+(?:·[가-힣0-9]+)+")
DOT_LONG = 5                                     # 가운뎃점 양쪽 낱말이 이보다 짧으면 붙여 읽는다(심의·의결 → 심의 의결)


def classify(line: str) -> tuple[str, str]:
    """대본 한 줄 → (종류, 표식을 뗀 본문). 종류: h1 h2 h3 box n1 n2 n3 p"""
    for mark, kind in (("### ", "h3"), ("## ", "h2"), ("# ", "h1"), ("> ", "box")):
        if line.startswith(mark):
            return kind, line[len(mark):].strip()
    if line[0] in CIRCLED:
        return "n1", line
    if line[0] in SUB_MARKS or re.match(r"\d\. ", line):
        return "n2", line
    if line[0] in THIRD_MARKS or re.match(r"[가-바]\. ", line):
        return "n3", line
    return "p", line


def parse(path: Path = SCRIPT) -> list[dict]:
    """대본 → [{no, ch, title, pages, items:[{k, d(화면), sp(낭독)}]}]"""
    if not path.exists():
        raise FileNotFoundError(f"대본 없음: {path}")
    tracks: list[dict] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("@@"):
            continue
        if line.startswith("==="):
            parts = [p.strip() for p in line.strip("= ").split("|")]
            if len(parts) != 4:
                raise ValueError(f"{n}행: 트랙 머리는 '=== 번호 | 챕터 | 제목 | 쪽' 형식이어야 합니다: {line}")
            no, ch, title, pages = parts
            ch_said = re.sub(r"^(\d+)장", r"제\1장", ch)          # "4장"을 "네 장"으로 읽지 않게
            intro = {"k": "t", "d": f"{ch} · {title}", "sp": f"{speak_plain(ch_said)}. {speak_plain(title)}."}
            tracks.append({"no": no, "ch": ch, "title": title, "pages": pages, "items": [intro]})
            continue
        if not tracks:
            raise ValueError(f"{n}행: 트랙 머리(===) 앞에 본문이 있습니다")
        kind, body = classify(line)
        tracks[-1]["items"].append({"k": kind, "d": OVERRIDE.sub(r"\1", body), "sp": speak(kind, body)})
    if not tracks:
        raise ValueError("트랙이 하나도 없습니다")
    return tracks


def read_paren(m: re.Match) -> str:
    inner, josa = m.group(1).strip(), m.group(2)
    if josa and inner.endswith(SENTENCE_END):
        return f"{josa}, {inner}, "            # 소방공무원(…포함한다)으로서 → 소방공무원으로서, …포함한다,
    return f", {inner}{josa}" if josa else f", {inner}, "


def read_dots(m: re.Match) -> str:
    words = m.group(0).split("·")
    joined = len(words) == 2 and all(len(w) < DOT_LONG for w in words)
    return (" " if joined else ", ").join(words)


def speak_plain(t: str) -> str:
    """기호를 말로: 화면 전용 제거, 괄호·가운뎃점·날짜·119 처리."""
    t = OVERRIDE.sub(r"\2", t)
    t = SCREEN_ONLY.sub("", t)
    t = re.sub(r"[「」\"“”'‘’]", "", t)
    t = SIDO.sub("시도", t).replace("119", "일일구")
    t = DATE.sub(lambda m: f"{m.group(1)}년 {int(m.group(2))}월 {int(m.group(3))}일", t)
    for i, ch in enumerate(CIRCLED, 1):          # 문장 속 ④ → 4번
        t = t.replace(ch, f"{i}번")
    for _ in range(3):                            # 괄호가 잇따라 나오는 경우까지
        t = PAREN.sub(read_paren, t)
    t = DOT_RUN.sub(read_dots, t).replace("·", ", ").replace(" : ", ". ")
    t = re.sub(r"(\d)(이상|이하|이내)", r"\1 \2", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([,.])", r"\1", t)
    t = re.sub(r",\s*\.", ".", t)
    t = re.sub(r"(,\s*)+", ", ", t)
    return t.strip(" ,")


def lead_word(kind: str, body: str) -> tuple[str, str]:
    """항목 머리표 → (읽을 순서말, 머리표를 뗀 본문)"""
    if kind == "n1":
        return ORD_1[CIRCLED.index(body[0])], body[1:]
    if kind == "n2" and body[0] in SUB_MARKS:
        return ORD_2[SUB_MARKS.index(body[0])], body[1:]
    if kind == "n2":
        return ORD_2[int(body[0]) - 1], body[2:]
    if kind == "n3" and body[0] in THIRD_MARKS:
        return ORD_3[THIRD_MARKS.index(body[0])], body[1:]
    if kind == "n3":
        return body[0], body[2:]
    return "", body


def speak(kind: str, body: str) -> str:
    """대본 한 줄 → 낭독문."""
    if kind in ("h2", "h3"):                      # 제목의 번호 "1", "(1)" 는 읽지 않는다
        body = re.sub(r"^\(?\d+\)?\s+", "", body)
    lead, rest = lead_word(kind, body)
    t = speak_plain(rest)
    if kind.startswith("h"):
        t = t.replace(", ", ". ", 1) if "(" in body else t
    t = f"{lead}, {t}" if lead else t
    return t if re.search(r"[.?!]$", t) else t + "."


# ── 교재 OCR 대조 ─────────────────────────────────────────────
def norm(t: str) -> str:
    return re.sub(r"[^가-힣0-9]", "", t)


def page_range(pages: str) -> list[int]:
    out: list[int] = []
    for part in pages.replace("쪽", "").split(","):
        a, _, b = part.strip().partition("~")
        out += list(range(int(a), int(b or a) + 1))
    return out


def verify() -> int:
    """대본 각 줄의 글자 조각이 해당 쪽 OCR에 있는 비율. 낮은 줄만 출력하고 그 수를 돌려준다."""
    if not OCR_TXT.exists():
        raise FileNotFoundError(f"OCR 텍스트 없음: {OCR_TXT} (pdftotext -raw 로 먼저 추출)")
    ocr_pages = OCR_TXT.read_text(encoding="utf-8").split("\f")
    low = 0
    for tr in parse():
        src = norm("".join(ocr_pages[p + PAGE_OFFSET - 1] for p in page_range(tr["pages"])))
        scores = []
        for it in tr["items"][1:]:
            if "교재 26쪽" in it["d"] or "부분만" in it["d"]:
                continue                           # 다른 쪽(비교표)에서 온 줄·안내 줄
            body = norm(SCREEN_ONLY.sub("", lead_word(it["k"], it["d"])[1]))
            grams = [body[i:i + VERIFY_GRAM] for i in range(max(1, len(body) - VERIFY_GRAM + 1))]
            score = sum(g in src for g in grams) / len(grams)
            scores.append(score)
            if score < VERIFY_MIN:
                low += 1
                print(f"  [{tr['no']}] {score:.2f} {it['d'][:90]}")
        print(f"{tr['no']} {tr['title']}: 평균 일치 {sum(scores) / len(scores):.3f} ({len(scores)}줄)")
    return low


def main() -> None:
    tracks = parse()
    if "--verify" in sys.argv:
        print(f"일치율 {VERIFY_MIN} 미만 {verify()}줄")
        return
    for tr in tracks:
        chars = sum(len(i["sp"]) for i in tr["items"])
        print(f"{tr['no']} [{tr['ch']}] {tr['title']} ({tr['pages']}) — {len(tr['items'])}줄, 낭독 {chars}자")
        if "--speech" in sys.argv:
            print("\n".join(f"   {i['k']:3s} {i['sp']}" for i in tr["items"]), "\n")
    print(f"합계 {sum(len(i['sp']) for t in tracks for i in t['items'])}자")


if __name__ == "__main__":
    main()
