"""4단계: 추출된 quote를 교재 PDF에 음영으로 적용 → 표시/ 폴더 저장
사용: python3 apply_marks.py [--books 10,12] [--out DIR] [--send]
"""
import difflib
import json
import sys
from collections import defaultdict
from pathlib import Path

import fitz

from common import (BOOKS, BOOK_DIR, EXAM_MARKS, ODD_MARKS, OUT_DIR, RESULT_DIR, char_stream,
                    load_jsonl, load_pages, norm, split_page_id, telegram_document, telegram_text)

COLORS = {"ans": (1, 0.92, 0.25), "wrong": (1, 0.5, 0.5), "odd": (0.45, 0.9, 0.45)}
SHORT = {"10": "화재1", "11": "화재2", "12": "구조", "13": "구급"}
FILL_OPACITY = 0.4   # 뷰어가 주석을 안 그려도 보이도록 본문에 직접 음영을 굽는다
TITLES = {"ans": "기출 정답/옳은 보기", "wrong": "기출 틀린 보기", "odd": "특이 포인트"}
PARTIAL_MIN = 0.6     # 부분 일치 허용 비율
MIN_CHARS = 8
TG_LIMIT = 49 * 1024 * 1024
FONT_FILE = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
LEGEND = [
    "소방전술 공통교재 — 1200제 기출 표시본",
    "",
    "     노랑 : 1200제 기출 정답·옳은 보기의 근거 원문 (메모: 문항번호·보기)",
    "     빨강 : 기출 틀린 보기가 비틀어 놓은 원문 (메모: 보기 → 원문 차이)",
    "     초록 : 기출은 아니지만 출제 가능성 높은 특이 포인트 (메모: 이유)",
    "",
    "음영을 누르면 메모가 뜹니다.",
    "문항번호 tsNN-MM = 플래시카드 전술 챕터 NN의 MM번.",
]


def line_union(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    out = []
    for r in sorted(rects, key=lambda r: (round(r.y0 / 3), r.x0)):
        if out and (min(r.y1, out[-1].y1) - max(r.y0, out[-1].y0) > 0.4 * (r.y1 - r.y0)):
            out[-1] |= r
        else:
            out.append(fitz.Rect(r))
    return out


def locate(chars: list, quote: str) -> tuple[list[fitz.Rect], str]:
    """quote 위치 → (줄단위 rect들, 'exact'|'partial'|'miss')"""
    s = "".join(c for c, _ in chars)
    nk = norm(quote)
    if len(nk) < MIN_CHARS:
        return [], "miss"
    i = s.find(nk)
    if i >= 0:
        return line_union([chars[j][1] for j in range(i, i + len(nk))]), "exact"
    m = difflib.SequenceMatcher(None, s, nk, autojunk=False).find_longest_match(0, len(s), 0, len(nk))
    if m.size >= max(MIN_CHARS, PARTIAL_MIN * len(nk)):
        return line_union([chars[j][1] for j in range(m.a, m.a + m.size)]), "partial"
    return [], "miss"


def collect(books: set[str]) -> dict[str, list[dict]]:
    """page_id → [{kind, quote, title, content}]"""
    by_page = defaultdict(list)
    for r in load_jsonl(EXAM_MARKS):
        for m in r["marks"]:
            kind = "wrong" if m.get("kind") == "wrong" else "ans"
            opt_ = m.get("opt", "")
            by_page[m["page"]].append({
                "kind": kind, "quote": m["quote"],
                "title": f"[기출 {r['qid']} {opt_}] {TITLES[kind]}",
                "content": f"{r['qid']} 보기{opt_} (정답 {r['ans']})\n{m.get('note', '')}".strip()})
    for r in load_jsonl(ODD_MARKS):
        for m in r["odd"]:
            by_page[m["page"]].append({"kind": "odd", "quote": m["quote"], "title": "💡 특이 포인트",
                                       "content": m.get("why", "")})
    return {k: v for k, v in by_page.items() if k.split(":")[0] in books}


def add_legend(doc: fitz.Document, stats: dict) -> None:
    page = doc.new_page(pno=0, width=doc[0].rect.width, height=doc[0].rect.height)
    lines = LEGEND + ["", f"표시 수: 노랑 {stats['ans']} / 빨강 {stats['wrong']} / 초록 {stats['odd']}"]
    y = 90
    for i, line in enumerate(lines):
        page.insert_text((50, y), line, fontfile=FONT_FILE, fontname="sdgothic",
                         fontsize=18 if i == 0 else 11)
        y += 30 if i == 0 else 20
    for kind, y0 in (("ans", 140), ("wrong", 160), ("odd", 180)):
        page.draw_rect(fitz.Rect(50, y0 - 10, 64, y0 + 2), color=COLORS[kind], fill=COLORS[kind])


def merge_overlaps(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    """같은 줄에서 실제로 겹치는(x·y 모두) rect만 합친다. 떨어진 구간은 따로 둔다."""
    out: list[fitz.Rect] = []
    for r in sorted(rects, key=lambda r: (round(r.y0 / 3), r.x0)):
        for i, o in enumerate(out):
            if (min(r.y1, o.y1) - max(r.y0, o.y0) > 0.4 * (r.y1 - r.y0)) and r.x0 <= o.x1 + 1 and r.x1 >= o.x0 - 1:
                out[i] = o | r
                break
        else:
            out.append(fitz.Rect(r))
    return out


def same_line(a: fitz.Rect, b: fitz.Rect) -> bool:
    return min(a.y1, b.y1) - max(a.y0, b.y0) > 0.4 * (a.y1 - a.y0)


def subtract(r: fitz.Rect, painted: list[fitz.Rect]) -> list[fitz.Rect]:
    """r에서 이미 칠한 같은 줄 구간을 가로로 빼서 남는 조각들을 돌려준다 (색 섞임 방지)."""
    pieces = [fitz.Rect(r)]
    for o in painted:
        if not same_line(r, o):
            continue
        nxt = []
        for pc in pieces:
            if pc.x1 <= o.x0 or pc.x0 >= o.x1:
                nxt.append(pc)
                continue
            if pc.x0 < o.x0:
                nxt.append(fitz.Rect(pc.x0, pc.y0, o.x0, pc.y1))
            if pc.x1 > o.x1:
                nxt.append(fitz.Rect(o.x1, pc.y0, pc.x1, pc.y1))
        pieces = nxt
    return [pc for pc in pieces if pc.width > 2]


def bake_fills(page: fitz.Page, fills: dict) -> None:
    """빨강 > 노랑 > 초록 우선순위로, 한 구간엔 한 색만 칠한다 (겹쳐서 갈색 되는 것 방지)."""
    painted: list[fitz.Rect] = []
    for kind in ("wrong", "ans", "odd"):
        for r in merge_overlaps(fills.get(kind, [])):
            for pc in subtract(r, painted):
                page.draw_rect(pc, color=None, fill=COLORS[kind], fill_opacity=FILL_OPACITY, overlay=True)
                painted.append(pc)


def apply_book(book: str, marks: dict, pages: dict, out_dir: Path) -> tuple[Path, dict, list]:
    doc = fitz.open(BOOK_DIR / BOOKS[book])
    stats = defaultdict(int)
    misses = []
    for pid, items in marks.items():
        _, pno = split_page_id(pid)
        page = doc[pno]
        chars = char_stream(page)
        fills = defaultdict(list)   # kind → rects (같은 색은 겹쳐도 한 번만 칠한다)
        for it in items:
            rects, how = locate(chars, it["quote"])
            if how == "miss":
                misses.append({"page": pid, "label": pages[pid]["label"], **it})
                continue
            fills[it["kind"]].extend(rects)
            an = page.add_highlight_annot(rects)
            an.set_colors(stroke=COLORS[it["kind"]])
            an.set_info(title=it["title"], content=it["content"] + (" (부분일치)" if how == "partial" else ""))
            an.set_opacity(0)   # 색은 본문에 구운 것만 보이고, 주석은 메모(클릭)용
            an.update()
            stats[it["kind"]] += 1
            stats[how] += 1
        bake_fills(page, fills)
    add_legend(doc, stats)
    out = out_dir / f"{SHORT[book]}_기출표시.pdf"
    doc.save(out, garbage=1, deflate=True)
    shrink_if_needed(out)
    doc.close()
    return out, dict(stats), misses


def shrink_if_needed(out: Path) -> None:
    """텔레그램 50MB 초과 시 이미지 해상도를 낮춰 한 파일로 보낼 수 있게 줄인다."""
    if out.stat().st_size <= TG_LIMIT:
        return
    doc = fitz.open(out)
    doc.rewrite_images(dpi_threshold=180, dpi_target=120, quality=70)
    tmp = out.with_suffix(".tmp.pdf")
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    if tmp.stat().st_size < out.stat().st_size:
        tmp.replace(out)
    else:
        tmp.unlink()


def send(out: Path, caption: str) -> str:
    if out.stat().st_size <= TG_LIMIT:
        return "전송 OK" if telegram_document(out, caption) else "전송 실패"
    doc = fitz.open(out)
    half = len(doc) // 2
    results = []
    for i, (a, b) in enumerate(((0, half - 1), (half, len(doc) - 1)), 1):
        part = fitz.open()
        part.insert_pdf(doc, from_page=a, to_page=b)
        p = out.with_name(out.stem + f"_{i}부.pdf")
        part.save(p, garbage=1, deflate=True)
        results.append("OK" if telegram_document(p, f"{caption} ({i}부, {a + 1}~{b + 1}쪽)") else "실패")
    return "분할 전송 " + "/".join(results)


def opt(name: str, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> None:
    books = set(opt("--books", ",".join(BOOKS)).split(","))
    out_dir = Path(opt("--out", RESULT_DIR))
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = load_pages()
    marks = collect(books)
    report = {}
    for book in sorted(books):
        bm = {k: v for k, v in marks.items() if k.startswith(book + ":")}
        out, stats, misses = apply_book(book, bm, pages, out_dir)
        report[book] = {"file": str(out), "stats": stats, "misses": misses}
        print(book, out.name, stats, "miss", len(misses), flush=True)
        if "--send" in sys.argv:
            cap = (f"📗 {out.stem}\n🟡 기출근거 {stats.get('ans', 0)} / 🔴 틀린보기 {stats.get('wrong', 0)}"
                   f" / 🟢 특이 {stats.get('odd', 0)}")
            print(send(out, cap), flush=True)
    (OUT_DIR / "apply_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
