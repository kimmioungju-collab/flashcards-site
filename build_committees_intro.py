#!/usr/bin/env python3
"""챕터 핵심요약(public/intros/fsNN.json)에서 위원회 관련 블록만 뽑아 public/committees_intro.js 생성.
사용: python3 build_committees_intro.py  (요약이 바뀌면 다시 실행)"""
import json, re, pathlib

ROOT = pathlib.Path(__file__).parent / "public"
OUT = ROOT / "committees_intro.js"

# (챕터번호, 위원회 id(committees_data.js의 C.id), 제목, 추출방식, 찾을 문자열 목록)
SPEC = [
    (1, "인사",     "소방공무원인사위원회",      "h4", ["📌 소방공무원인사위원회"]),
    (2, "채용비위", "채용비위심의위원회",        "li", ["2) 채용비위심의위원회"]),
    (2, "임용심사", "임용심사위원회",            "li", ["6) 임용심사위원회"]),
    (4, "근평조정", "근무성적평정조정위원회",    "li", ["5) 평정의 조정"]),
    (5, "중앙승진", "승진심사위원회(중앙·보통)", "h4", ["📌 승진심사위원회"]),
    (5, "보통승진", "특별승진심사",              "h4", ["📌 특별승진심사"]),
    (6, "고충",     "고충심사위원회",            "h4", ["📌 고충심사"]),
    (6, "교육훈련", "소방교육훈련정책위원회",    "li", ["1) 소방교육훈련정책위원회"]),
    (7, "징계",     "징계위원회 · 회의",         "h4", ["📌 징계위원회", "📌 회의"]),
    (7, "소청",     "불복신청(소청심사위원회)",  "h4", ["📌 불복신청"]),
]
TAG = re.compile(r"<li>|</li>")


def intro(n):
    return json.load(open(ROOT / "intros" / f"fs{n:02d}.json", encoding="utf-8"))["intro"]


def h4block(s, title):
    i = s.find(f"<h4>{title}")
    if i < 0:
        raise ValueError(f"h4 못 찾음: {title}")
    j = s.find("<h4>", i + 4)
    return s[i:(j if j > 0 else len(s))]


def liblock(s, start):
    p = s.find(start)
    if p < 0:
        raise ValueError(f"li 못 찾음: {start}")
    i = s.rfind("<li>", 0, p + 1)
    depth, k = 0, i
    while True:
        m = TAG.search(s, k)
        if not m:
            raise ValueError(f"li 닫힘 없음: {start}")
        depth += 1 if m.group() == "<li>" else -1
        k = m.end()
        if depth == 0:
            return "<ul>" + s[i:k] + "</ul>"


def build():
    items = []
    for ch, cid, title, mode, keys in SPEC:
        s = intro(ch)
        html = "".join(h4block(s, k) if mode == "h4" else liblock(s, k) for k in keys)
        html = html.replace("<b>", "<mark>").replace("</b>", "</mark>")
        items.append({"ch": f"fs{ch:02d}", "chTitle": json.load(open(ROOT / "chapters" / f"fs{ch:02d}.json", encoding="utf-8"))["title"],
                      "id": cid, "title": title, "html": html})
    OUT.write_text("// 자동 생성: build_committees_intro.py — 챕터 핵심요약에서 위원회 블록만 추출\nconst INTRO=" +
                   json.dumps(items, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"{OUT.name}: {len(items)}개 블록, {OUT.stat().st_size:,} bytes")


if __name__ == "__main__":
    build()
