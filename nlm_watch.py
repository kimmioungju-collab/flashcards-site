#!/usr/bin/env python3
"""플래시카드 앱의 팟캐스트 요청 처리기.

앱이 RTDB `flashcards/audio/<ch>_<mode>` 에 {status:"pending"} 을 쓰면
이 스크립트가 NotebookLM 오디오를 만들어 Firebase Hosting에 올리고
같은 노드에 {status:"done", url:...} 을 채워 넣는다.

실행: python3 nlm_watch.py          # 대기 요청 1회 처리 후 종료 (크론용)
      python3 nlm_watch.py --loop   # 30초 간격 상주 실행
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
AUDIO_OUT = BASE / "public" / "audio" / "nlm"
LOCK = Path("/tmp/nlm_watch.lock")
DB = "https://work-schedule-dash-4ceb2-default-rtdb.firebaseio.com"
NODE = "/flashcards/audio"
SITE = "https://work-schedule-dash-4ceb2.web.app"
PROJECT = "work-schedule-dash-4ceb2"
POLL_SEC = 30
STALE_SEC = 3600  # running 상태로 1시간 넘게 방치된 요청은 실패 처리


def db(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{DB}{path}.json", data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode()
    return json.loads(raw) if raw and raw != "null" else None


def set_state(key: str, **fields):
    cur = db("GET", f"{NODE}/{key}") or {}
    cur.update(fields)
    cur["t"] = int(time.time() * 1000)
    db("PUT", f"{NODE}/{key}", cur)


def mp3_minutes(path: Path) -> int | None:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return max(1, round(float(r.stdout.strip()) / 60))
    except ValueError:
        return None


def deploy() -> None:
    r = subprocess.run(
        ["npx", "firebase-tools", "deploy", "--only", "hosting", "--project", PROJECT],
        cwd=str(BASE), capture_output=True, text=True, timeout=900,
    )
    if r.returncode != 0:
        raise RuntimeError("배포 실패: " + (r.stderr or r.stdout)[-300:])


def process(key: str, item: dict) -> None:
    ch = item.get("ch") or key.split("_")[0]
    mode = item.get("mode") or "full"
    print(f"[{time.strftime('%H:%M:%S')}] 처리 시작 {key}", flush=True)
    set_state(key, status="running", msg="요약을 정리하고 있어요")

    args = [sys.executable, str(BASE / "nlm_audio.py"), ch]
    if mode == "weak":
        args.append("weak")
    args.append("--no-send")

    set_state(key, status="running", msg="AI가 오디오를 만드는 중이에요 (10~30분)")
    r = subprocess.run(args, capture_output=True, text=True, timeout=5400)
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).strip().splitlines()
        set_state(key, status="error", msg=(tail[-1] if tail else "오디오 생성 실패")[:200])
        print("실패:", tail[-3:], flush=True)
        return

    src = Path.home() / "Downloads" / f"행정법_{ch}_{'오답' if mode == 'weak' else '전체'}요약.mp3"
    if not src.exists():
        set_state(key, status="error", msg="생성된 파일을 찾지 못했습니다")
        return

    AUDIO_OUT.mkdir(parents=True, exist_ok=True)
    dst = AUDIO_OUT / f"{key}.mp3"
    shutil.copy2(src, dst)

    set_state(key, status="running", msg="스크립트(자막)를 만드는 중이에요")
    try:  # 자막 실패해도 오디오 배포는 계속 진행
        subprocess.run(
            [sys.executable, str(BASE / "pod_script.py"), key],
            capture_output=True, text=True, timeout=3600,
        )
    except Exception as e:
        print("자막 생성 실패(무시):", e, flush=True)

    set_state(key, status="running", msg="업로드 중이에요")
    deploy()
    set_state(key, status="done", url=f"{SITE}/audio/nlm/{key}.mp3",
              mins=mp3_minutes(dst), size=dst.stat().st_size)
    print(f"[{time.strftime('%H:%M:%S')}] 완료 {key}", flush=True)


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


def run_once() -> None:
    for key, item in pending_items():
        try:
            process(key, item)
        except Exception as e:
            set_state(key, status="error", msg=str(e)[:200])
            print("오류:", e, flush=True)


def main() -> None:
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < STALE_SEC:
        print("이미 실행 중 — 종료", flush=True)
        return
    LOCK.write_text(str(time.time()))
    try:
        if "--loop" in sys.argv:
            while True:
                LOCK.write_text(str(time.time()))
                run_once()
                time.sleep(POLL_SEC)
        else:
            run_once()
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
