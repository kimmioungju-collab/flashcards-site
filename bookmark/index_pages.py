"""1단계: 교재 4권 쪽별 텍스트 추출 → pages.json"""
import json
import re

import fitz

from common import BOOKS, BOOK_DIR, PAGES_JSON, OUT_DIR, norm, page_id

LABEL_RE = re.compile(r"^\s*(\d{1,3})(\s{2,}|\s*$)")


def printed_label(text: str) -> str:
    """머리글의 인쇄 쪽번호(둘째 줄 숫자) 추출."""
    for line in text.splitlines()[:3]:
        m = LABEL_RE.match(line)
        if m:
            return m.group(1)
    return ""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pages = {}
    for book, fname in BOOKS.items():
        doc = fitz.open(BOOK_DIR / fname)
        for pno in range(len(doc)):
            text = doc[pno].get_text()
            pages[page_id(book, pno)] = {
                "book": book, "pno": pno, "label": printed_label(text),
                "head": text.splitlines()[0].strip() if text.strip() else "",
                "text": text, "nlen": len(norm(text)),
            }
        print(book, fname, len(doc), "쪽", flush=True)
    PAGES_JSON.write_text(json.dumps(pages, ensure_ascii=False))
    print("총", len(pages), "쪽 →", PAGES_JSON)


if __name__ == "__main__":
    main()
