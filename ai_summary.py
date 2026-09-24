#!/usr/bin/env python3
"""AI 취약 정리본 생성기.

앱이 RTDB `flashcards/aisum/<sync>_<ch>` 에 {status:"pending"} 을 쓰면
이 스크립트가 대상 문항(챕터 모드는 그날 푼 것 중 틀린 것)을 모아 claude CLI로 '헷갈림 진단 + 쉬운 설명
+ 암기법' 정리본(HTML)을 만들어 같은 노드에 {status:"done", html:...} 저장한다.

날짜 모드(`<sync>_dYYYY-MM-DD`)는 그날 푼 문항 중 취약한 것(그날 오답 + ⭐체크)을
전 챕터에서 모아 연관 주제끼리 묶어 정리한다.

실행: python3 ai_summary.py ch05              # 직접 생성 (기본 sync 1111)
      python3 ai_summary.py ch05 --day 2026-09-23   # 그날 오답만(기본: 최근 오답일)
      python3 ai_summary.py ch05 --all              # 누적 오답·⭐체크 전체
      python3 ai_summary.py ch05 --sync 1111
      python3 ai_summary.py 2026-09-23         # 날짜별 취약 정리본
      python3 ai_summary.py --watch            # 대기 요청 1회 처리 (크론용)
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = "https://work-schedule-dash-4ceb2-default-rtdb.firebaseio.com"
NODE = "/flashcards/aisum"
DEFAULT_SYNC = "1111"
CLAUDE = "/opt/homebrew/bin/claude"
LOCK = Path("/tmp/ai_summary_watch.lock")
STALE_SEC = 1800  # running 상태 30분 초과 시 실패 처리
EXP_CAP = 700     # 문제당 해설 최대 길이(프롬프트 비대 방지)
MAX_ITEMS = 90    # 날짜 모드에서 한 번에 다룰 최대 문항 수(오답 많은 순)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUBJECTS = (("ch", "행정법"), ("fs", "공무원법"), ("ts", "전술"), ("hz", "위험물"), ("rv", "복습노트"))


def db(method: str, path: str, body: dict | None = None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(f"{DB}{path}.json", data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode()
    return json.loads(raw) if raw and raw != "null" else None


def node_key(sync: str, ch: str) -> str:
    safe = re.sub(r"[.#$\[\]/]", "_", sync or "anon")
    return f"{safe}_{ch}"


def set_state(key: str, **fields):
    cur = db("GET", f"{NODE}/{key}") or {}
    cur.update(fields)
    cur["t"] = int(time.time() * 1000)
    db("PUT", f"{NODE}/{key}", cur)


def norm_ch(ch: str) -> str:
    ch = ch.strip().lower()
    if not ch.startswith("ch"):
        ch = "ch" + ch
    return "ch" + ch[2:].zfill(2)


def load_chapter(ch: str) -> dict:
    p = BASE / "public" / "chapters" / f"{ch}.json"
    if not p.exists():
        raise FileNotFoundError(f"챕터 파일 없음: {p.name}")
    return json.loads(p.read_text(encoding="utf-8"))


def chapter_day_req(ch: str, sync: str, day: str = "") -> dict | None:
    """CLI용 — 그 챕터에서 '그날 푼 것 중 틀린' 문항 요청 본문. 날짜 미지정이면 가장 최근 오답일."""
    wlog = db("GET", f"/flashcards/{sync}/wlog") or {}
    days = [day] if day else sorted(wlog, reverse=True)
    for d in days:
        nos = sorted((wlog.get(d) or {}).get(ch) or {})
        if nos:
            qstat = db("GET", f"/flashcards/{sync}/ch/{ch}/qstat") or {}
            stat = {n: {"w": (qstat.get(n) or {}).get("w", 0) or 0,
                        "m": 1 if (qstat.get(n) or {}).get("m") else 0} for n in nos}
            return {"nos": nos, "stat": stat, "day": d}
    return None


def weak_list(ch: str, sync: str, req: dict | None) -> list[dict]:
    """오답·⭐체크 문제 목록. 앱이 보낸 nos/stat이 있으면 그것을 우선한다."""
    chap = load_chapter(ch)
    req = req or {}
    req_nos = req.get("nos")
    req_stat = req.get("stat") or {}

    if req_nos:
        nos = {str(n) for n in req_nos}
        qstat, last_wrong = req_stat, set()
    else:
        cloud = db("GET", f"/flashcards/{sync}/ch/{ch}") or {}
        qstat = cloud.get("qstat") or {}
        last_wrong = set(cloud.get("wrongNos") or [])
        nos = None

    out = []
    for q in chap.get("questions", []):
        no = str(q.get("no", ""))
        st = qstat.get(no) or {}
        w = st.get("w", 0) or 0
        m = bool(st.get("m"))
        pick = (no in nos) if nos is not None else (w > 0 or m or no in last_wrong)
        if pick:
            out.append({
                "no": no, "label": ch_label(ch), "grade": q.get("grade", ""),
                "theme": q.get("theme", ""),
                "q": q.get("q", ""), "ans": q.get("ans", ""),
                "exp": (q.get("exp", "") or "")[:EXP_CAP],
                "w": w, "m": m, "dayTag": "",
            })
    out.sort(key=lambda x: (x["w"], x["m"]), reverse=True)
    return out


RULES = """[출력 규칙 — 반드시 지킬 것]
- HTML 조각만 출력하세요. 마크다운·코드펜스·<html>·<head>·<body> 금지. 첫 글자부터 태그로 시작.
- 허용 태그: <h4> <p> <ul> <li> <b> <mark> <table> <tr> <th> <td> <div class="tip"> <div class="trap">
- 구성:
  1) <h4>🧭 헷갈림 진단</h4> — 반복되는 실수 패턴 2~3가지. 어느 문항에서 그 패턴이 나왔는지 번호를 같이 적으세요.
     (예: "허가·특허·인가의 효과를 서로 바꿔 기억함 — {ex}")
  2) <b>연관 묶음</b>이 핵심입니다. {group}
     각 소단원마다:
     <h4>① 소단원 제목 (연관 문항: {sample})</h4>
     <p><b>⚡ 핵심 한 줄:</b> 이것만 외우면 묶음 전체가 풀리는 문장</p>
     <div class="trap">🚨 함정 포인트: X지문이 어느 단어를 바꿔치기했는지, 문항끼리 어떻게 교차로 헷갈리는지</div>
     <p>💡 <b>쉽게 이해:</b> 일상 비유로 한 번에 이해시키기</p>
     <div class="tip">🧠 암기법: 두문자·연상 문장 등 구체적 암기 장치 (묶음 전체를 한 번에 커버할 것)</div>
     혼동 개념 쌍(취소vs철회, 기속력vs기판력 등)은 반드시 <table>로 좌우 비교
  3) <h4>🔗 묶어서 외울 것</h4> — 소단원 간 연결 고리를 <ul>로 3~5줄
  4) <h4>📌 풀기 직전 3분 복습</h4> — 한 줄 초압축 <ul>, 핵심 키워드는 <mark>로 강조
- 말투: 쉽고 친근한 존댓말. 조문 번호 나열보다 이해·암기 중심.
- {numbering}
- ❌누적 2회 이상 문항은 더 자세히, 나머지는 간결하게. 전체 분량은 과하지 않게."""


def fmt_items(weak: list[dict], cross: bool) -> str:
    """문항 블록 — cross=True면 챕터 라벨을 붙여 어느 챕터 문항인지 구분한다."""
    items = []
    for x in weak:
        tag = f"❌누적{x['w']}회" if x["w"] else ""
        tag += " ⭐체크" if x["m"] else ""
        if x.get("dayTag"):
            tag += x["dayTag"]
        head = f"[{x['label']} {x['no']}번]" if cross else f"[문항 {x['no']}]"
        items.append(
            f"{head} ({x['grade']}) {x['theme']} {tag}\n"
            f"문제: {x['q']}\n정답: {x['ans']}\n해설: {x['exp']}"
        )
    return "\n\n".join(items)


def build_prompt(title: str, weak: list[dict], day: str = "") -> str:
    """챕터 취약 정리본 — 같은 챕터 안에서 쟁점이 닿는 문항끼리 묶는다."""
    rules = RULES.format(
        ex="12·15번",
        group="같은 쟁점·비교 대상·함정 유형이면 한 소단원으로 묶으세요. 문항 순서대로가 아니라 주제로 재배열합니다.",
        sample="03·12, 27",
        numbering="문항 번호는 '03번'처럼 적으세요.",
    )
    when = f"{day} 하루 동안 푼 문항 중 틀린 것만" if day else "틀렸거나(오답) 헷갈려서 ⭐체크한 문제만"
    head = f"[대상] {day} 오답" if day else "[대상] 누적 오답·⭐체크"
    return f"""당신은 소방승진 수험 전문 과외 선생님입니다. 학생이 {when} 모아 왔습니다.
이 학생이 '무엇을 왜 헷갈렸는지' 진단하고, **연관되는 문항끼리 묶어서** 한 번에 외울 수 있게 정리해 주세요.
학생은 이 정리본을 외운 뒤 곧바로 같은 문항들로 오답 풀이를 합니다.

[챕터] {title}
{head}
[대상 문항 {len(weak)}개 — 누적 오답 횟수 많은 순]
{fmt_items(weak, False)}

{rules}"""


def clean_html(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"^```(?:html)?\s*|\s*```$", "", s, flags=re.M).strip()
    i = s.find("<")
    if i > 0:
        s = s[i:]
    return s.strip()


def generate(ch: str, sync: str, req: dict | None = None) -> None:
    ch = norm_ch(ch)
    key = node_key(sync, ch)
    chap = load_chapter(ch)
    title = (req or {}).get("title") or chap.get("title", ch)

    day = (req or {}).get("day") or ""
    weak = weak_list(ch, sync, req)
    if not weak:
        set_state(key, status="error", msg="오답·⭐체크 문제가 없습니다")
        print(f"{ch}: 취약 문항 없음")
        return

    set_state(key, status="running", ch=ch, sync=sync, day=day,
              msg=f"{day + ' 오답 ' if day else '취약 '}{len(weak)}문항을 AI가 분석하는 중이에요")
    prompt = build_prompt(title, weak, day)

    r = subprocess.run(
        [CLAUDE, "-p", "--model", "claude-fable-5-1"],
        input=prompt, capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()
        set_state(key, status="error", msg=(tail[-1] if tail else "AI 호출 실패")[:200])
        print(f"{ch}: claude 실패 —", tail[-2:])
        return

    html = clean_html(r.stdout)
    if len(html) < 300 or "<h4" not in html:
        set_state(key, status="error", msg="AI 출력이 올바르지 않습니다 — 다시 시도해 주세요")
        print(f"{ch}: 출력 검증 실패 ({len(html)}자)")
        return

    set_state(key, status="done", ch=ch, sync=sync, html=html, day=day,
              weakCount=len(weak), title=title)
    print(f"{ch}: 완료 — {day or '누적'} 대상 {len(weak)}문항, {len(html)}자")


# ── 날짜별 취약 정리본 ───────────────────────────────────────────────
def ch_label(ch: str) -> str:
    """chNN → '행정법 5장' 처럼 사람이 읽는 라벨."""
    for pre, name in SUBJECTS:
        if ch.startswith(pre):
            return f"{name} {ch[len(pre):].lstrip('0') or ch[len(pre):]}장"
    return ch


def date_targets(date: str, sync: str) -> dict[str, dict]:
    """그날 푼 문항 중 취약한 것 = 그날 오답(wlog) + 그날 푼 문항 중 ⭐체크.

    반환: {chNN: {"문항no": {"w": 누적오답, "m": 체크여부, "day": 그날오답여부}}}
    """
    wlog = db("GET", f"/flashcards/{sync}/wlog/{date}") or {}
    dq = db("GET", f"/flashcards/{sync}/dq/{date}") or {}
    out: dict[str, dict] = {}
    for ch in sorted(set(wlog) | set(dq)):
        qstat = (db("GET", f"/flashcards/{sync}/ch/{ch}/qstat") or {})
        picked = {}
        for no in (wlog.get(ch) or {}):
            st = qstat.get(str(no)) or {}
            picked[str(no)] = {"w": st.get("w", 0) or 0, "m": bool(st.get("m")), "day": True}
        for no in (dq.get(ch) or {}):                    # 그날 푼 문항 중 ⭐체크한 것 보충
            st = qstat.get(str(no)) or {}
            if bool(st.get("m")) and str(no) not in picked:
                picked[str(no)] = {"w": st.get("w", 0) or 0, "m": True, "day": False}
        if picked:
            out[ch] = picked
    return out


def req_targets(req: dict) -> dict[str, dict]:
    """앱이 보낸 items=[{ch,no,w,m}] 를 date_targets 와 같은 모양으로 바꾼다."""
    out: dict[str, dict] = {}
    for it in req.get("items") or []:
        ch, no = str(it.get("ch") or ""), str(it.get("no") or "")
        if not ch or not no:
            continue
        out.setdefault(ch, {})[no] = {
            "w": it.get("w", 0) or 0, "m": bool(it.get("m")), "day": True,
        }
    return out


def date_weak(targets: dict[str, dict]) -> list[dict]:
    """대상 문항을 챕터 파일에서 읽어 취약 목록으로 만든다(오답 많은 순, 상한 MAX_ITEMS)."""
    out = []
    for ch, nos in targets.items():
        try:
            chap = load_chapter(ch)
        except FileNotFoundError:
            continue
        label = ch_label(ch)
        for q in chap.get("questions", []):
            no = str(q.get("no", ""))
            hit = nos.get(no)
            if not hit:
                continue
            out.append({
                "ch": ch, "label": label, "no": no,
                "grade": q.get("grade", ""), "theme": q.get("theme", ""),
                "q": q.get("q", ""), "ans": q.get("ans", ""),
                "exp": (q.get("exp", "") or "")[:EXP_CAP],
                "w": hit["w"], "m": hit["m"], "day": hit["day"],
            })
    out.sort(key=lambda x: (x["day"], x["w"], x["m"]), reverse=True)
    return out[:MAX_ITEMS]


def build_date_prompt(date: str, weak: list[dict]) -> str:
    """날짜 취약 정리본 — 챕터가 달라도 쟁점이 닿는 문항끼리 묶는다."""
    for x in weak:
        x["dayTag"] = " (그날 오답)" if x["day"] else " (그날 푼 체크문항)"
    chs = sorted({x["label"] for x in weak})
    rules = RULES.format(
        ex="행정법 68장 12·15번",
        group="챕터가 달라도 같은 쟁점·비교 대상이면 한 소단원으로 묶으세요.",
        sample="행정법 68장 03·12, 행정법 66장 07",
        numbering="문항 번호는 반드시 '챕터 라벨 + 번호'로 적으세요(여러 챕터가 섞여 있으므로).",
    )
    return f"""당신은 소방승진 수험 전문 과외 선생님입니다. 학생이 {date} 하루 동안 푼 문항 중
틀렸거나 ⭐체크한 것만 모아 왔습니다. 여러 챕터가 섞여 있습니다.
이 학생이 오늘 '무엇을 왜 헷갈렸는지' 진단하고, **연관되는 문항끼리 묶어서** 한 번에 외울 수 있게 정리해 주세요.
학생은 이 정리본을 외운 뒤 곧바로 같은 문항들로 오답 풀이를 합니다.

[대상 날짜] {date}
[관련 챕터] {", ".join(chs)}
[취약 문항 {len(weak)}개 — 그날 오답 우선, 누적 오답 많은 순]
{fmt_items(weak, True)}

{rules}"""


def generate_date(date: str, sync: str, req: dict | None = None) -> None:
    if not DATE_RE.match(date):
        raise ValueError(f"날짜 형식이 올바르지 않습니다: {date}")
    req = req or {}
    key = node_key(sync, req.get("scope") or ("d" + date))
    targets = req_targets(req) or date_targets(date, sync)
    weak = date_weak(targets)
    if not weak:
        set_state(key, status="error", mode="date", date=date,
                  msg=f"{date}에 오답·⭐체크 문항이 없습니다")
        print(f"{date}: 취약 문항 없음")
        return

    set_state(key, status="running", mode="date", date=date, sync=sync,
              msg=f"{date} 취약 {len(weak)}문항을 AI가 묶는 중이에요")
    r = subprocess.run(
        [CLAUDE, "-p", "--model", "claude-fable-5-1"],
        input=build_date_prompt(date, weak), capture_output=True, text=True, timeout=900,
    )
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()
        set_state(key, status="error", mode="date", date=date,
                  msg=(tail[-1] if tail else "AI 호출 실패")[:200])
        print(f"{date}: claude 실패 —", tail[-2:])
        return

    html = clean_html(r.stdout)
    if len(html) < 300 or "<h4" not in html:
        set_state(key, status="error", mode="date", date=date,
                  msg="AI 출력이 올바르지 않습니다 — 다시 시도해 주세요")
        print(f"{date}: 출력 검증 실패 ({len(html)}자)")
        return

    chs = sorted({x["ch"] for x in weak})
    set_state(key, status="done", mode="date", date=date, sync=sync, html=html,
              weakCount=len(weak), title=f"{date} 취약 정리본",
              chapters=",".join(chs))
    print(f"{date}: 완료 — 취약 {len(weak)}문항({len(chs)}챕터), {len(html)}자")


def pending_items() -> list[tuple[str, dict]]:
    all_items = db("GET", NODE) or {}
    out = []
    now = time.time() * 1000
    for key, item in all_items.items():
        if not isinstance(item, dict):
            continue
        if item.get("status") == "pending":
            out.append((key, item))
        elif item.get("status") == "running" and now - item.get("t", 0) > STALE_SEC * 1000:
            set_state(key, status="error", msg="시간 초과 — 다시 시도해 주세요")
    return out


def watch_once() -> None:
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < STALE_SEC:
        print("이미 실행 중 — 종료")
        return
    LOCK.write_text(str(time.time()))
    try:
        for key, item in pending_items():
            ch = item.get("ch") or key.rsplit("_", 1)[-1]
            sync = item.get("sync") or key.rsplit("_", 1)[0]
            print(f"[{time.strftime('%H:%M:%S')}] 처리 시작 {key}")
            try:
                if item.get("mode") == "date" or DATE_RE.match(ch.lstrip("d")[:10]):
                    generate_date(item.get("date") or ch.lstrip("d")[:10], sync, item)
                else:
                    generate(ch, sync, item)
            except Exception as e:
                set_state(key, status="error", msg=str(e)[:200])
                print("오류:", e)
    finally:
        LOCK.unlink(missing_ok=True)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if "--watch" in args:
        watch_once()
        return
    sync = DEFAULT_SYNC
    if "--sync" in args:
        i = args.index("--sync")
        sync = args[i + 1]
        args = args[:i] + args[i + 2:]
    if DATE_RE.match(args[0]):
        generate_date(args[0], sync)
        return
    ch = norm_ch(args[0])
    if "--all" in args:                       # 누적 오답·⭐체크 전체로 만들고 싶을 때만
        generate(ch, sync)
        return
    day = args[args.index("--day") + 1] if "--day" in args else ""
    req = chapter_day_req(ch, sync, day)
    if req is None:
        print(f"{ch}: {day or '최근'} 오답 기록이 없습니다 (--all 로 누적 전체 생성 가능)")
        return
    generate(ch, sync, req)


if __name__ == "__main__":
    main()
