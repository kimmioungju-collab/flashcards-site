#!/usr/bin/env python3
"""챕터 핵심요약 + 기출 OX → 섹션별 강의 대본(claude) → Airy TTS(기본; gemini·edge 선택 가능) → 앱용 오디오 세트.
NotebookLM 팟캐스트(+Whisper 자막 정렬)를 대체한다. 섹션별로 따로 합성하므로
타임라인이 정확하고(자막 정렬 불필요), 대본이 곧 자막이라 오탈자·환각이 없다.

기본 모드는 "완독": 핵심요약(상세)을 한 글자도 빼지 않고 섹션 순서대로 그대로 읽는다 (사용자 요청 2026-09-09).
--lecture 를 주면 claude가 기출 지문까지 녹인 강의 대본을 새로 쓴다.

사용법:
  python3 lecture_pod.py ch47              # 완독 → public/audio/nlm/ch47_full.{mp3,sections.json,script.json}
  python3 lecture_pod.py ts14              # 전술·소방공무원법(fs) 챕터도 동일
  python3 lecture_pod.py ch47 --lecture    # claude 강의 대본 모드
  python3 lecture_pod.py fs01 --coach      # 1타 강사·성우 톤 암기 대본(두음법칙·리마인드 퀴즈) 모드
  python3 lecture_pod.py ch47 weak         # (--lecture 에서만 의미) 오답·체크 문항 중심 → ch47_weak.*
  python3 lecture_pod.py ch47 --dry        # 대본만 만들고 TTS 생략 (대본 검수용)
  python3 lecture_pod.py ch47 --reuse      # /tmp/lecture_pod/<key>/script.txt 대본 재사용 (음성만 다시)

출력 형식은 앱(app.html podScriptLoad)이 이미 읽는 형식 그대로:
  <key>.sections.json  {"sections":[{"t":제목,"h":HTML}], "tl":[{"s":시작초,"e":끝초,"i":섹션번호}]}
  <key>.script.json    [{"s":시작초,"e":끝초,"t":문장}]
"""
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import edge_tts
from html import unescape as html_unescape

from nlm_audio import build_summary, load_chapter, weak_nos
from pod_align import split_sections

BASE = Path(__file__).resolve().parent
INTRO_DIR = BASE / "public" / "intros"
OUT_DIR = BASE / "public" / "audio" / "nlm"
TMP_ROOT = Path("/tmp/lecture_pod")

# ── TTS 백엔드: 1순위 구글 Gemini TTS(자연스러운 강의 톤), 429/오류 시 edge-tts 폴백 ──
TTS_ENGINE = os.environ.get("LECTURE_TTS", "airy")               # airy(기본, 2026-09-13 사용자 결정) | gemini | edge
USE_GEMINI = TTS_ENGINE == "gemini"
# ── Airy Studio TTS (https://airy.so/studio) — 무로그인 API, 640자/요청, 분당 15요청 ──
AIRY_URL = "https://api.airy.so/v1/studio/speech"
AIRY_VOICE = os.environ.get("AIRY_VOICE", "a597bb7a98fc9ec1")    # Silvia(여성, #1). 목록: https://api.airy.so/v1/studio/voices?lang=ko
AIRY_STYLE = "normal"                                             # normal | bright | calm | whisper
AIRY_MAX_CHARS = 640
AIRY_RPM_GAP = 4.2                                                # 분당 15회 → 요청 간 최소 간격(초)
AIRY_PARALLEL = 2
AIRY_ANON_FILE = Path("/tmp/airy_anon_id")
GEMINI_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_VOICE = os.environ.get("LECTURE_VOICE", "Charon")       # 남성·차분. 여성은 Kore/Aoede 등
GEMINI_STYLE = "차분하고 또렷한 강사 말투로, 너무 빠르지 않게 또박또박 읽어주세요:\n"
# ── 강조(암기 포인트) 낭독: 요약의 <b> 중 숫자·판정어가 든 문장은 따로 힘주어 읽고, 볼륨↑·앞뒤 정적·핵심어 반복·차임 삽입 ──
EMPH_STYLE = ("지금부터 읽는 문장은 시험에 반드시 나오는 핵심 암기 포인트입니다. 학생 뇌리에 박히도록, 강사가 칠판을 두드리듯 힘주어, "
              "평소보다 천천히, 숫자와 핵심어를 한 글자씩 또렷하고 크게 강조해서 읽어주세요. 지시문은 읽지 말고 문장만 읽으세요:\n")
EMPH_GAIN_DB = 5                   # 강조 문장 볼륨 증폭(dB) — Gemini 강조 톤은 평소보다 ~2.5dB 작게 나와 보정
EMPH_PAD_MS = (350, 450)           # 강조 문장 앞·뒤 정적(ms)
EMPH_EDGE = {"rate": "-8%", "volume": "+30%"}   # edge-tts 폴백 시 강조 파라미터
EMPH_MAX_REPEAT = 3                # 문장 끝에 "다시 한 번." 하고 되풀이할 핵심어 최대 수
EMPH_MAX_CHARS = 120               # 이보다 긴 문장(개요·나열)은 강조하지 않음
EMPH_DING = True                   # 강조 문장 앞 짧은 차임
EM_OPEN, EM_CLOSE = "«", "»"      # 대본 안 강조 마커 (요약 <b> → 마커; 이모지 제거 범위 밖 문자)
EM_UNIT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:년|개월|월|일|시간|세|퍼센트|회|배|명|분|주|억|만|호|조|항)(?!의)")
EM_VERDICT_RE = re.compile(r"^(틀립니다|아닙니다|옳습니다|맞습니다|틀린 지문|옳은 지문|포함|제외|우선)$")
GEMINI_RPM_GAP = 6.5               # 무료 티어 분당 10회 → 요청 간 최소 간격(초)
GEMINI_PARALLEL = 2                # 동시 합성 수
ENV_FILE = Path.home() / "telegram-claude-bridge" / ".env"
VOICE = os.environ.get("LECTURE_EDGE_VOICE", "ko-KR-InJoonNeural")   # edge-tts 음성 (fs 강의는 SunHi — 사용자 선택 2026-09-13)
RATE = "+6%"
CHARS_PER_MIN = 330                # 한국어 낭독 속도 (RATE 반영 대략치)
MIN_MINUTES, MAX_MINUTES = 5, 14
CHUNK_CHARS = int(os.environ.get("LECTURE_CHUNK", 260))   # TTS 1회 합성 길이 (문장 경계에서 자름) — 길수록 중간 발음이 뭉개져(사용자 보고 2026-09-13) 160 권장
GAP_SEC = 0.7                      # 섹션 사이 무음
MIN_SEC_PER_CHAR = 0.09            # 합성 결과 검증: 이보다 짧으면 누락으로 보고 재합성 (한국어 낭독 ≈ 0.13~0.17s/자)
TTS_RETRY = 3
CLAUDE_MODEL = os.environ.get("LECTURE_CLAUDE", "sonnet")

SCRIPT_PROMPT = """아래는 시험 대비 자료입니다. [섹션 목록]은 앱 화면에 표시되는 핵심 요약이고, [기출 지문]은 실제 출제된 OX 지문입니다.
강사 한 명이 혼자 낭독하는 한국어 강의 대본을 **섹션별로** 작성하세요. 학생은 화면에서 해당 섹션을 보며 듣습니다.

[출력 형식 — 반드시 준수]
- 섹션마다 반드시 `[[SEC n]]` 한 줄(n은 섹션 번호, 0부터)을 먼저 쓰고, 그 아래에 그 섹션의 낭독문을 씁니다.
- 섹션 번호 0부터 {last}까지 **빠짐없이, 순서대로** 모두 출력합니다. 하나라도 빠지면 안 됩니다.
- 마지막 섹션 [[SEC {last}]] 끝에는 "마지막으로 이 챕터에서 가장 자주 틀리는 함정을 다시 짚겠습니다." 로 시작해 함정 3~5개를 짚는 마무리를 붙입니다.
- 낭독용 순수 평문만. 마크다운·특수기호(#, *, -, ①, →, ·, ~, §) 금지(«» 강조 마커만 예외). 소제목·문항번호 표기 금지. 괄호·따옴표 최소화. 문장은 짧게.
- 법령 조문은 "제36조 제1항"처럼 말로 풀어서. 숫자는 "60일", "3퍼센트"처럼 읽히게. O와 X는 "옳다", "틀리다"로.
- 인사말·자기소개·마무리 인사 금지. 대본 외 설명·주석 절대 금지.

[내용 규칙]
1. 각 섹션의 낭독문은 그 섹션 요약의 모든 항목을 빠짐없이 다루되, 화면 글을 그대로 읽지 말고 이유와 맥락을 붙여 설명합니다.
2. [기출 지문]은 관련 섹션 안에서 자연스럽게 녹입니다. 틀린 지문은 "시험에서 ~라고 나오면 틀린 겁니다. 옳은 표현은 ~입니다" 형태로 짝지어 대비합니다.
3. 헷갈리는 두 개념은 "A는 ~인 반면, B는 ~입니다" 비교 형식으로.
4. 기간·요건·숫자·법령명·판례 결론은 또박또박, 중요한 것은 한 번 더 반복.
5. 각 섹션 끝에 "한 줄 정리." 로 시작하는 암기 문장 하나.
7. 시험에 자주 나오는 숫자·기간·요건·판정이 든 문장은 문장 전체를 «와 »로 감쌉니다(섹션당 2~4문장). 이 문장은 강조 낭독됩니다.
6. 전체 분량 약 {chars}자(낭독 약 {mins}분), ±15% 이내. 섹션별 분량은 그 섹션 내용량에 비례.
"""


# ── --coach: 1타 강사·오디오북 성우 톤의 암기용 낭독 대본 (사용자 프롬프트 2026-09-13) ──
COACH_PROMPT = """너는 자격증 시험 합격을 돕는 1타 강사이자 오디오북 성우야.
내가 제시하는 [기본 요약] 내용을 바탕으로, 듣기만 해도 저절로 암기되는 '음성 낭독용 핵심 요약 스크립트'를 작성해 줘.

[스크립트 작성 규칙]
1. 내용 누락 금지: 제공된 [기본 요약]의 수치, 공식, 조건, 핵심 개념은 100% 반영할 것.
2. 실전 시험 맞춤: 주관식/실기 시험 답안지에 바로 적어낼 수 있는 형태로 키워드를 묶어주고, 두음법칙(앞글자 따기)이나 연상 기법을 적극적으로 만들어 줄 것.
3. 낭독 최적화: TTS 프로그램이나 사람이 읽을 때 호흡이 꼬이지 않도록 짧고 간결한 구어체(~습니다, ~하죠)로 작성할 것.
4. 구조 및 기호 활용:
   - 출제 포인트: 도입부에서 이 내용이 왜 중요한지 환기 (ex. "이건 매번 나오는 단골 문제입니다.")
   - 강조: 힘주어 읽어야 할 핵심 키워드는 쉼표나 말줄임표로 호흡을 분리 (ex. "가장 중요한 건, 바로... '수신기'입니다.")
   - 리마인드 퀴즈: 스크립트 마지막에 방금 들은 내용을 3초 안에 떠올리게 하는 짧은 질문 배치

[출력 형식 — 앱 연동용, 반드시 준수]
- [기본 요약]은 [[SEC n]] 으로 나뉜 섹션 목록입니다. 학생은 화면에서 해당 섹션을 보며 듣습니다.
- 섹션마다 반드시 `[[SEC n]]` 한 줄(n은 섹션 번호, 0부터)을 먼저 쓰고, 그 아래에 그 섹션의 낭독문을 씁니다.
- 섹션 번호 0부터 {last}까지 **빠짐없이, 순서대로** 모두 출력합니다. 하나라도 빠지면 안 됩니다.
- [[SEC 0]] 첫머리에 이 챕터 전체의 출제 포인트 환기를 넣고, 각 섹션도 한 문장으로 왜 중요한지 짚고 시작합니다.
- 리마인드 퀴즈는 마지막 섹션 [[SEC {last}]] 끝에 "리마인드 퀴즈." 로 시작해 질문 3~5개를 넣고, 각 질문 뒤 "정답은... "으로 짧게 답을 붙입니다.
- 낭독용 순수 평문만. 마크다운·특수기호(#, *, -, ①, →, ·, ~, §, 대괄호) 금지(«» 강조 마커만 예외). "출제 포인트", "강조" 같은 규칙 이름을 글자로 쓰지 말고 말로 표현합니다.
- 시험에 반드시 나오는 숫자·기간·요건·주체가 든 핵심 문장은 문장 전체를 «와 »로 감쌉니다(섹션당 2~4문장, 한 문장 120자 이내). 이 문장은 힘주어 낭독됩니다.
- 법령 조문은 "제36조 제1항"처럼 말로 풀어서. 숫자는 "60일", "3퍼센트"처럼 읽히게. 괄호·따옴표 최소화.
- 인사말·자기소개·마무리 인사 금지. 대본 외 설명·주석 절대 금지.
- 전체 분량은 낭독 약 {mins}분(약 {chars}자) 안팎이지만, 규칙 1(내용 100% 반영)이 분량보다 우선합니다.
"""


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def norm_chapter(raw: str) -> str:
    """ch47 / fs10 / ts14 / 47 → 표준 ID (nlm_audio.norm_chapter 는 ch 전용이라 fs·ts 가 깨진다)."""
    c = raw.strip().lower()
    m = re.fullmatch(r"(ch|fs|ts)?0*(\d+)", c)
    if not m:
        sys.exit(f"ERROR: 챕터 ID 형식 오류 — {raw}")
    return f"{m.group(1) or 'ch'}{int(m.group(2)):02d}"


# ── 1. 자료 준비 ─────────────────────────────────────────────
def load_sections(ch: str) -> list[dict]:
    path = INTRO_DIR / f"{ch}.json"
    if not path.exists():
        sys.exit(f"ERROR: 핵심요약 없음 — {path} (요약이 있어야 섹션 강의를 만들 수 있습니다)")
    data = json.loads(path.read_text(encoding="utf-8"))
    html = (data.get("intro") or "").strip()
    if not html:
        sys.exit("ERROR: intro(상세 요약)가 비어 있습니다")
    secs = split_sections(html)
    if not secs:
        sys.exit("ERROR: 섹션 분리 실패")
    return secs


def target_minutes(n_q: int, n_sec: int) -> int:
    return max(MIN_MINUTES, min(MAX_MINUTES, round(n_q * 0.18 + n_sec * 0.4)))


def build_material(secs: list[dict], summary: str) -> str:
    lines = ["[섹션 목록]"]
    for i, s in enumerate(secs):
        lines += [f"[[SEC {i}]] {s['t']}", s["text"].strip(), ""]
    # build_summary 의 2부(기출 지문)만 잘라 붙인다
    part2 = summary.split("# 2부", 1)
    quiz = ("# 2부" + part2[1]) if len(part2) == 2 else summary
    lines += ["", "[기출 지문]", quiz]
    return "\n".join(lines)


def build_coach_material(secs: list[dict]) -> str:
    lines = ["[기본 요약]"]
    for i, s in enumerate(secs):
        lines += [f"[[SEC {i}]] {s['t']}", s["text"].strip(), ""]
    return "\n".join(lines)


# ── 2. 대본 ─────────────────────────────────────────────────
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2190-\u21FF\u2500-\u257F]")
ORDINAL = ["첫째", "둘째", "셋째", "넷째", "다섯째", "여섯째", "일곱째", "여덟째", "아홉째", "열째"]


def speech_text(fragment: str) -> str:
    """HTML 조각 → 낭독문. 요약을 생략 없이 그대로 읽되 기호만 말로 바꾼다."""
    t = re.sub(r"<br\s*/?>", "\n", fragment)
    t = re.sub(r"</(td|th)>\s*<(td|th)[^>]*>", ", ", t)          # 표 셀
    t = re.sub(r"</(li|p|tr|h4|div)>", "\n", t)
    t = re.sub(r"<(b|strong)>(.*?)</\1>", EM_OPEN + r"\2" + EM_CLOSE, t, flags=re.S)   # 굵은 글씨 = 암기 포인트 마커
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(rf"\s*([.!?,:])\s*{EM_CLOSE}", EM_CLOSE + r"\1", t)                       # «틀립니다.» → «틀립니다». (문장 분리용)
    t = html_unescape(t)
    t = t.replace("→", " 그 다음 ").replace("↔", " 와 ").replace(" — ", ", ").replace("—", ", ")   # 화살표는 이모지 제거 전에 말로
    t = EMOJI_RE.sub(" ", t)
    for i, ch in enumerate("①②③④⑤⑥⑦⑧⑨⑩"):
        t = t.replace(ch, f" {ORDINAL[i]}, ")
    t = (t.replace("·", ", ").replace("§", "제")
           .replace("「", "").replace("」", "").replace("『", "").replace("』", "")
           .replace("%", "퍼센트").replace(" vs ", " 대 ").replace("vs.", "대").replace("＋", " 플러스 ").replace("+", " 플러스 ")
           .replace("≠", " 은 다르다 ").replace("=", " 는 ").replace("OX", "오엑스").replace("O지문", "옳은 지문").replace("X지문", "틀린 지문"))
    t = re.sub(r"(\d)\s*~\s*(\d)", r"\1에서 \2", t)               # 5~15 → 5에서 15
    t = t.replace("~", " ")
    t = re.sub(r"[#*_`>|\[\]{}]", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    lines = [ln.strip(" ,") for ln in t.split("\n")]
    lines = [ln if re.search(r"[.!?]$", ln) else ln + "." for ln in lines if ln]
    return "\n".join(lines)


def verbatim_script(secs: list[dict]) -> list[str]:
    """섹션별 완독 대본: 제목을 먼저 읽고 본문을 그대로 읽는다."""
    out = []
    for s in secs:
        body = re.sub(r"<h4[^>]*>.*?</h4>", "", s["h"], count=1, flags=re.S)
        title = speech_text(s["t"]).rstrip(".")
        out.append(f"{title}.\n{speech_text(body)}".strip())
    return out


FILLER_RE = re.compile(r"(?:(?<=^)|(?<=[.!?]\s)|(?<=\n))(?:자|네|그럼|이제 자),\s*")   # 문장 첫머리 감탄사 — TTS가 "자아~"로 늘여 읽음


def clean_for_tts(t: str) -> str:
    t = re.sub(r"\[\[SEC \d+\]\]", "", t)
    t = FILLER_RE.sub("", t)
    t = re.sub(r"\.{2,}|…", ",", t)                                    # 말줄임표 → 쉼표 호흡
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r"[#*_`>|\[\]{}]", "", t)
    t = t.replace("→", " 에서 ").replace("·", ", ").replace("§", "제").replace("~", "부터 ")
    for i, ch in enumerate("①②③④⑤⑥⑦⑧⑨⑩", 1):
        t = t.replace(ch, f"{i}번, ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def parse_script(raw: str, n_sec: int) -> list[str] | None:
    parts = re.split(r"^\s*\[\[SEC\s*(\d+)\]\]\s*$", raw, flags=re.M)
    # parts: [prefix, n, text, n, text, ...]
    found: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        try:
            found[int(parts[i])] = parts[i + 1].strip()
        except ValueError:
            continue
    missing = [i for i in range(n_sec) if not found.get(i)]
    if missing:
        log(f"대본 섹션 누락: {missing}")
        return None
    return [clean_for_tts(found[i]) for i in range(n_sec)]


def make_script(material: str, n_sec: int, mins: int, template: str = SCRIPT_PROMPT) -> list[str]:
    prompt = template.format(last=n_sec - 1, chars=mins * CHARS_PER_MIN, mins=mins)
    for attempt in range(1, 3):
        log(f"대본 작성 중 (목표 {mins}분, 섹션 {n_sec}개, claude {CLAUDE_MODEL}, {attempt}회차)")
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", CLAUDE_MODEL],
            input=material, capture_output=True, text=True, timeout=1500,
        )
        secs = parse_script(r.stdout or "", n_sec) if r.returncode == 0 else None
        if secs and sum(map(len, secs)) >= 800:
            log(f"대본 완성: {sum(map(len, secs))}자")
            return secs
        log(f"대본 실패 rc={r.returncode} err={(r.stderr or '')[:200]}")
    sys.exit("ERROR: 대본 생성 2회 실패")


# ── 3. TTS ──────────────────────────────────────────────────
def marks_in(sent: str) -> list[str]:
    return [m.strip() for m in re.findall(f"{EM_OPEN}(.*?){EM_CLOSE}", sent, flags=re.S)]


def strip_marks(t: str) -> str:
    return t.replace(EM_OPEN, "").replace(EM_CLOSE, "")


def is_emphasis(sent: str) -> bool:
    """강조 낭독 대상: 문장 전체 마커 / '한 줄 정리' / 숫자·판정어가 든 굵은 글씨."""
    if len(strip_marks(sent)) > EMPH_MAX_CHARS:
        return False
    if re.fullmatch(rf"{EM_OPEN}[^{EM_CLOSE}]*{EM_CLOSE}[.!?]?", sent):   # 문장 하나가 통째로 굵은 글씨
        return True
    if sent.startswith("한 줄 정리"):
        return True
    return any(re.search(r"\d", m) or EM_VERDICT_RE.match(m.rstrip(".!?")) for m in marks_in(sent))


def key_terms(sent: str) -> list[str]:
    """문장 끝에 되풀이할 핵심어: 굵은 글씨 속 '60일' 같은 숫자+단위, 없으면 12자 이내 숫자 구절."""
    terms: list[str] = []
    for m in marks_in(sent):
        found = EM_UNIT_RE.findall(m) or ([m.strip(" .,")] if re.search(r"\d", m) and len(m) <= 12 else [])
        terms += [f for f in found if f not in terms]
    return terms[:EMPH_MAX_REPEAT]


def plan_chunks(text: str) -> list[tuple[str, bool]]:
    """문장 → (합성 텍스트, 강조 여부). 일반 문장은 CHUNK_CHARS 로 묶고 강조 문장은 단독 청크 + 핵심어 반복."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", text) if s.strip()]
    chunks: list[tuple[str, bool]] = []
    cur = ""
    for s in sents:
        if is_emphasis(s):
            if cur:
                chunks.append((cur, False))
                cur = ""
            terms = key_terms(s)
            tail = (" 다시 한 번. " + ". ".join(terms) + ".") if terms else ""
            chunks.append((strip_marks(s) + tail, True))
            continue
        s = strip_marks(s)
        if cur and len(cur) + len(s) + 1 > CHUNK_CHARS:
            chunks.append((cur, False))
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append((cur, False))
    return chunks


def emphasize_audio(path: Path) -> None:
    """강조 청크 후처리: 볼륨 증폭 + 앞뒤 정적."""
    tmp = path.with_name(path.stem + "_em.mp3")
    pre, post = EMPH_PAD_MS
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                    "-af", f"volume={EMPH_GAIN_DB}dB,alimiter=limit=0.89:level=false,adelay={pre}:all=1,apad=pad_dur={post / 1000}",   # 리미터: 증폭 후 클리핑 방지(-1dB)
                    "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", str(tmp)], check=True)
    tmp.replace(path)


def duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def gemini_key() -> str:
    k = os.environ.get("GEMINI_API_KEY", "")
    if not k and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                k = line.split("=", 1)[1].strip()
    return k


GEM = {"key": gemini_key() if USE_GEMINI else "", "off": False, "last": 0.0, "lock": None, "sem": None}
AIRY = {"on": TTS_ENGINE == "airy", "off": False, "last": 0.0, "lock": None, "sem": None}


def airy_anon_id() -> str:
    if AIRY_ANON_FILE.exists():
        return AIRY_ANON_FILE.read_text().strip()
    import uuid
    aid = str(uuid.uuid4())
    AIRY_ANON_FILE.write_text(aid)
    return aid


def airy_synth_sync(text: str, out: Path) -> None:
    """Airy TTS 1회 호출 → wav → mp3. 429는 Retry-After 만큼 쉬고 1회 재시도, 4xx(입력 오류)는 즉시 예외."""
    if len(text) > AIRY_MAX_CHARS:
        raise RuntimeError(f"Airy 640자 초과: {len(text)}자")
    body = {"model": "airy-tts-v1", "input": text, "voice": AIRY_VOICE, "language": "ko",
            "style": AIRY_STYLE, "response_format": "wav", "speed": 1}
    headers = {"Content-Type": "application/json", "X-Studio-Anonymous-Id": airy_anon_id(),
               "X-Studio-Activity-Source": "generate", "Origin": "https://airy.so", "Referer": "https://airy.so/studio",
               "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"}   # Cloudflare 1010: python-urllib UA 차단
    for attempt in (1, 2):
        req = urllib.request.Request(AIRY_URL, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                wav = r.read()
            break
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="ignore")[:200]
            if e.code == 429 and attempt == 1:
                wait = float(e.headers.get("Retry-After") or 20)
                log(f"  Airy 429 → {wait:.0f}초 대기 후 재시도")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Airy HTTP {e.code}: {msg}")
    raw = out.with_suffix(".wav")
    raw.write_bytes(wav)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
                    "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", str(out)], check=True)
    raw.unlink(missing_ok=True)


async def airy_synth(text: str, out: Path) -> None:
    """RPM 제한(요청 간격) + 동시 수 제한을 지키며 Airy 호출."""
    if AIRY["lock"] is None:
        AIRY["lock"] = asyncio.Lock(); AIRY["sem"] = asyncio.Semaphore(AIRY_PARALLEL)
    async with AIRY["sem"]:
        async with AIRY["lock"]:
            wait = AIRY["last"] + AIRY_RPM_GAP - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            AIRY["last"] = time.time()
        await asyncio.to_thread(airy_synth_sync, text, out)


def gemini_synth_sync(text: str, out: Path, style: str = GEMINI_STYLE) -> None:
    """Gemini TTS 1회 호출 → PCM → mp3. 429/키 없음이면 GEM['off']=True 로 폴백 전환."""
    body = {"contents": [{"parts": [{"text": style + text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}}}}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEM['key']}",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=180))
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="ignore")[:200]
        if e.code == 429:
            GEM["off"] = True
            log(f"  Gemini 쿼터 초과(429) → 나머지는 edge-tts로 전환")
        raise RuntimeError(f"Gemini HTTP {e.code}: {msg}")
    part = r["candidates"][0]["content"]["parts"][0]["inlineData"]
    pcm = base64.b64decode(part["data"])
    raw = out.with_suffix(".pcm")
    raw.write_bytes(pcm)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1",
                    "-i", str(raw), "-c:a", "libmp3lame", "-b:a", "64k", str(out)], check=True)
    raw.unlink(missing_ok=True)


async def gemini_synth(text: str, out: Path, style: str = GEMINI_STYLE) -> None:
    """RPM 제한(요청 간격) + 동시 수 제한을 지키며 Gemini 호출."""
    if GEM["lock"] is None:
        GEM["lock"] = asyncio.Lock(); GEM["sem"] = asyncio.Semaphore(GEMINI_PARALLEL)
    async with GEM["sem"]:
        async with GEM["lock"]:
            wait = GEM["last"] + GEMINI_RPM_GAP - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            GEM["last"] = time.time()
        await asyncio.to_thread(gemini_synth_sync, text, out, style)


async def tts_chunk(text: str, out: Path, emph: bool = False) -> float:
    """합성 + 검증(길이/문자 비율). Gemini 우선, 실패·쿼터 시 edge-tts. emph=강조 낭독+후처리. 반환: 길이(초)."""
    for attempt in range(1, TTS_RETRY + 1):
        use_airy = AIRY["on"] and not AIRY["off"] and attempt < TTS_RETRY     # 마지막 시도는 무조건 edge 폴백
        use_gemini = not use_airy and bool(GEM["key"]) and not GEM["off"] and attempt < TTS_RETRY
        engine = "airy" if use_airy else "gemini" if use_gemini else "edge"
        try:
            out.unlink(missing_ok=True)
            if use_airy:
                await airy_synth(text, out)
            elif use_gemini:
                await gemini_synth(text, out, EMPH_STYLE if emph else GEMINI_STYLE)
            elif emph:
                await edge_tts.Communicate(text, VOICE, **EMPH_EDGE).save(str(out))
            else:
                await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(out))
            d = duration(out) if out.exists() else 0.0
            if d >= len(text) * MIN_SEC_PER_CHAR:
                if emph:
                    emphasize_audio(out)
                    d = duration(out)
                return d
            log(f"  합성 결과 의심(길이 {d:.1f}s / {len(text)}자, {engine}) → 재시도 {attempt}")
        except Exception as e:  # 네트워크·쿼터 등
            log(f"  합성 오류({attempt}, {engine}): {str(e)[:160]}")
        await asyncio.sleep(1.5 * attempt)
    raise RuntimeError(f"TTS {TTS_RETRY}회 실패: {text[:40]}…")


def make_ding(path: Path) -> None:
    """강조 문장 앞 짧고 부드러운 차임(E6, 0.22초)."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=1318:duration=0.22",
         "-af", "afade=t=out:st=0.03:d=0.19,volume=-14dB", "-ar", "24000", "-ac", "1",
         "-c:a", "libmp3lame", "-b:a", "48k", str(path)], check=True,
    )


def make_silence(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", str(GAP_SEC), "-c:a", "libmp3lame", "-b:a", "48k", str(path)], check=True,
    )


async def synth_all(sections: list[str], tmp: Path) -> tuple[list[Path], list[dict], list[dict]]:
    """섹션별 청크 합성 → (파일 순서, 섹션 타임라인, 문장 타임라인)."""
    silence = tmp / "gap.mp3"
    make_silence(silence)
    gap = duration(silence) or GAP_SEC
    ding = tmp / "ding.mp3"
    make_ding(ding)
    ding_len = duration(ding) if EMPH_DING else 0.0
    files, tl, script = [], [], []
    t = 0.0
    plans = [plan_chunks(s) for s in sections]
    total = sum(map(len, plans))
    n_em = sum(e for p in plans for _, e in p)
    log(f"청크 {total}개 (강조 {n_em}개)")
    done = 0
    for i, chunks in enumerate(plans):
        start = t
        outs = [tmp / f"s{i:02d}_{j:03d}.mp3" for j in range(len(chunks))]
        durs = await asyncio.gather(*(tts_chunk(c, f, e) for (c, e), f in zip(chunks, outs)))   # 섹션 안에서는 병렬, 순서는 유지
        for (chunk, emph), f, d in zip(chunks, outs, durs):
            if emph and EMPH_DING:
                files.append(ding)
                t += ding_len
            files.append(f)
            script.append({"s": round(t, 2), "e": round(t + d, 2), "t": chunk, **({"em": 1} if emph else {})})
            t += d
            done += 1
        log(f"  TTS {done}/{total} (섹션 {i}, {sum(durs):.1f}s, {'airy' if AIRY['on'] and not AIRY['off'] else 'gemini' if GEM['key'] and not GEM['off'] else 'edge'})")
        tl.append({"s": round(start, 2), "e": round(t, 2), "i": i})
        if i < len(sections) - 1:
            files.append(silence)
            t += gap
    return files, tl, script


def concat(files: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{f}'\n" for f in files), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", str(out)], check=True,   # 재인코딩: 조각별 인코딩 차이로 인한 DTS 경고·재생 튐 방지
    )


# ── 4. 메인 ─────────────────────────────────────────────────
def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ch = norm_chapter(sys.argv[1])
    weak = "weak" in sys.argv[2:]
    dry = "--dry" in sys.argv[2:]
    key = f"{ch}_{'weak' if weak else 'full'}"

    lecture = "--lecture" in sys.argv[2:]
    coach = "--coach" in sys.argv[2:]
    chap = load_chapter(ch)
    secs = load_sections(ch)
    log(f"{key}: 섹션 {len(secs)}개, 모드 {'강의(claude)' if lecture else '1타강사(claude)' if coach else '완독'}")

    tmp = TMP_ROOT / key
    tmp.mkdir(parents=True, exist_ok=True)
    saved = tmp / "script.txt"
    texts = None
    if "--reuse" in sys.argv[2:] and saved.exists():
        texts = parse_script(saved.read_text(encoding="utf-8"), len(secs))
        log(f"저장된 대본 재사용: {saved}" if texts else "저장된 대본이 섹션 수와 안 맞아 새로 작성")
    if not texts and coach:
        texts = make_script(build_coach_material(secs), len(secs),
                            target_minutes(len(chap["questions"]), len(secs)), COACH_PROMPT)
    if not texts and lecture:
        only = weak_nos(ch) if weak else None
        summary, n_q = build_summary(chap, ch, only)
        texts = make_script(build_material(secs, summary), len(secs), target_minutes(n_q, len(secs)))
    if not texts:
        texts = verbatim_script(secs)
        log(f"완독 대본: {sum(map(len, texts))}자 (요약 원문 그대로)")
    (tmp / "script.txt").write_text("\n\n".join(f"[[SEC {i}]]\n{t}" for i, t in enumerate(texts)), encoding="utf-8")
    if dry:
        log(f"DRY — 대본만 저장: {tmp / 'script.txt'}")
        return

    log(f"TTS 합성 시작 ({'Airy ' + AIRY_VOICE if AIRY['on'] else 'Gemini ' + GEMINI_VOICE if GEM['key'] else 'edge ' + VOICE})")
    files, tl, script = asyncio.run(synth_all(texts, tmp))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mp3 = OUT_DIR / f"{key}.mp3"
    concat(files, mp3)
    total = duration(mp3)
    if total < 60:
        sys.exit(f"ERROR: 결과 오디오가 {total:.0f}초 — 이상")
    (OUT_DIR / f"{key}.sections.json").write_text(
        json.dumps({"sections": [{"t": s["t"], "h": s["h"]} for s in secs], "tl": tl}, ensure_ascii=False),
        encoding="utf-8")
    (OUT_DIR / f"{key}.script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    log(f"DONE {mp3.name} {total/60:.1f}분 {mp3.stat().st_size//1024}KB, 섹션 {len(tl)}, 문장 {len(script)}")


if __name__ == "__main__":
    main()
