"""전술 교재 기출 음영 파이프라인 공통 모듈."""
import json
import re
import subprocess
from pathlib import Path

import fitz

BOOK_DIR = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/전술"
BOOKS = {
    "10": "10. 소방전술1(화재1).pdf",
    "11": "11. 소방전술1(화재2).pdf",
    "12": "12. 소방전술2(구조)-저용량.pdf",
    "13": "13. 소방전술3(구급)-저용량.pdf",
}
SITE = Path.home() / "projects/flashcards-site"
CHAPTER_DIR = SITE / "public/chapters"
OUT_DIR = SITE / "audit/bookmark"
PAGES_JSON = OUT_DIR / "pages.json"
EXAM_MARKS = OUT_DIR / "exam_marks.jsonl"
ODD_MARKS = OUT_DIR / "odd_marks.jsonl"
RESULT_DIR = BOOK_DIR / "표시"
ENV_FILE = Path.home() / "telegram-claude-bridge/.env"

CLAUDE = "/opt/homebrew/bin/claude"
MODEL = "claude-opus-4-8"
TIMEOUT = 600
MIN_PAGE_TEXT = 120  # 이보다 짧은 쪽은 그림/표지/목차로 보고 건너뜀

KEEP = re.compile(r"[0-9가-힣a-zA-Z%]")


def norm(s: str) -> str:
    return "".join(c for c in s if KEEP.match(c))


def page_id(book: str, pno: int) -> str:
    return f"{book}:{pno}"


def split_page_id(pid: str) -> tuple[str, int]:
    book, pno = pid.split(":")
    return book, int(pno)


def claude(prompt: str) -> str:
    r = subprocess.run(
        [CLAUDE, "-p", "--model", MODEL, "--output-format", "text", "--tools", ""],
        input=prompt, capture_output=True, text=True, timeout=TIMEOUT, cwd="/tmp",
    )
    if r.returncode != 0 and not (r.stdout and r.stdout.strip()):
        raise RuntimeError(f"claude 실패: {(r.stderr or r.stdout)[-300:]}")
    return r.stdout


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSON 없음: " + text[:200])
    return json.loads(m.group(0), strict=False)


def load_pages() -> dict:
    return json.loads(PAGES_JSON.read_text())


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for l in path.read_text().splitlines():
        if not l.strip():
            continue
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            continue  # 다른 프로세스가 쓰는 중인 마지막 줄
    return out


def append_jsonl(path: Path, obj: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def telegram_env() -> tuple[str, str]:
    env = dict(l.split("=", 1) for l in ENV_FILE.read_text().splitlines()
               if "=" in l and not l.startswith("#"))
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = env.get("ALLOWED_USERS", "").split(",")[0].strip()
    if not token or not chat:
        raise RuntimeError(".env에 TELEGRAM_BOT_TOKEN/ALLOWED_USERS 없음")
    return token, chat


def telegram_text(text: str) -> None:
    token, chat = telegram_env()
    subprocess.run(["curl", "-s", f"https://api.telegram.org/bot{token}/sendMessage",
                    "-d", f"chat_id={chat}", "--data-urlencode", f"text={text[:3900]}"],
                   capture_output=True, timeout=60)


def telegram_document(path: Path, caption: str) -> bool:
    token, chat = telegram_env()
    r = subprocess.run(["curl", "-s", f"https://api.telegram.org/bot{token}/sendDocument",
                        "-F", f"chat_id={chat}", "-F", f"document=@{path}",
                        "-F", f"caption={caption[:1000]}"],
                       capture_output=True, text=True, timeout=600)
    return '"ok":true' in (r.stdout or "")


def char_stream(page: fitz.Page) -> list[tuple[str, fitz.Rect]]:
    """[(글자, rect)] — 정규화 대상 글자만, 읽기 순서대로."""
    chars = []
    for b in page.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                for c in sp["chars"]:
                    if KEEP.match(c["c"]):
                        chars.append((c["c"], fitz.Rect(c["bbox"])))
    return chars
