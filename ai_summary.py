#!/usr/bin/env python3
"""AI 취약 정리본 생성기.

앱이 RTDB `flashcards/aisum/<sync>_<ch>` 에 {status:"pending"} 을 쓰면
이 스크립트가 오답·⭐체크 문제를 모아 claude CLI로 '헷갈림 진단 + 쉬운 설명
+ 암기법' 정리본(HTML)을 만들어 같은 노드에 {status:"done", html:...} 저장한다.

실행: python3 ai_summary.py ch05              # 직접 생성 (기본 sync 1111)
      python3 ai_summary.py ch05 --sync 1111
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
                "no": no, "grade": q.get("grade", ""), "theme": q.get("theme", ""),
                "q": q.get("q", ""), "ans": q.get("ans", ""),
                "exp": (q.get("exp", "") or "")[:EXP_CAP],
                "w": w, "m": m,
            })
    out.sort(key=lambda x: (x["w"], x["m"]), reverse=True)
    return out


def build_prompt(title: str, weak: list[dict]) -> str:
    items = []
    for x in weak:
        tag = f"❌{x['w']}회" if x["w"] else ""
        tag += " ⭐체크" if x["m"] else ""
        items.append(
            f"[문항 {x['no']}] ({x['grade']}) {x['theme']} {tag}\n"
            f"문제: {x['q']}\n정답: {x['ans']}\n해설: {x['exp']}"
        )
    data = "\n\n".join(items)
    return f"""당신은 행정법 수험 전문 과외 선생님입니다. 학생이 틀렸거나(오답) 헷갈려서 ⭐체크한 문제들만 모아 왔습니다.
이 학생이 '무엇을 왜 헷갈리는지'를 콕 짚어 진단하고, 쉽게 이해시키고, 암기까지 시키는 취약 정리본을 만들어 주세요.

[챕터] {title}
[취약 문항 {len(weak)}개 — 오답 횟수 많은 순]
{data}

[출력 규칙 — 반드시 지킬 것]
- HTML 조각만 출력하세요. 마크다운·코드펜스·<html>·<head>·<body> 금지. 첫 글자부터 태그로 시작.
- 허용 태그: <h4> <p> <ul> <li> <b> <mark> <table> <tr> <th> <td> <div class="tip"> <div class="trap">
- 구성:
  1) <h4>🧭 헷갈림 진단</h4> — 이 학생이 반복해서 헷갈리는 패턴을 2~3가지로 요약 (예: "허가·특허·인가의 효과를 서로 바꿔 기억함"). 오답 횟수(❌) 많은 문제에서 패턴을 찾으세요.
  2) 비슷한 개념끼리 묶어 소단원 구성 — 각 소단원마다:
     <h4>① 소단원 제목 (관련 문항: 03, 12)</h4>
     <p><b>⚡ 핵심 한 줄:</b> 이것만 기억하면 되는 문장</p>
     <div class="trap">🚨 함정 포인트: X지문이 어느 단어를 바꿔치기하는지, 학생이 정확히 어디서 헷갈렸는지</div>
     <p>💡 <b>쉽게 이해:</b> 일상 비유로 풀어서 설명</p>
     <div class="tip">🧠 암기법: 두문자·연상 문장·리듬 등 구체적인 암기 장치</div>
     혼동 개념 쌍(예: 허가vs특허, 취소vs철회)은 반드시 <table>로 좌우 비교
  3) <h4>📌 시험 직전 3분 복습</h4> — <ul>로 한 줄 초압축 리스트 (각 줄에 핵심 키워드를 <mark>로 강조)
- 말투: 쉽고 친근한 존댓말. 조문 번호 나열보다 이해 중심.
- ❌2회 이상 문제는 더 자세히, 나머지는 간결하게. 전체 분량은 과하지 않게."""


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

    weak = weak_list(ch, sync, req)
    if not weak:
        set_state(key, status="error", msg="오답·⭐체크 문제가 없습니다")
        print(f"{ch}: 취약 문항 없음")
        return

    set_state(key, status="running", ch=ch, sync=sync,
              msg=f"취약 {len(weak)}문항을 AI가 분석하는 중이에요")
    prompt = build_prompt(title, weak)

    r = subprocess.run(
        [CLAUDE, "-p", "--model", "sonnet"],
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

    set_state(key, status="done", ch=ch, sync=sync, html=html,
              weakCount=len(weak), title=title)
    print(f"{ch}: 완료 — 취약 {len(weak)}문항, {len(html)}자")


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
    generate(args[0], sync)


if __name__ == "__main__":
    main()
