#!/usr/bin/env python3
"""팟캐스트 자막(Whisper) ↔ 핵심요약(intro) 섹션 정렬 → .sections.json 생성.

Whisper 자막은 오타가 많아 화면 표시용으로 부적합하므로,
앱은 대신 핵심요약 섹션을 보여주고 재생 위치에 맞는 섹션을 하이라이트한다.
이 스크립트는 자막 세그먼트를 문자 바이그램 유사도 + Viterbi(전이 페널티)로
intro 섹션에 매핑해 타임라인을 만든다.

출력: public/audio/nlm/<key>.sections.json
  {"sections":[{"t":"제목","h":"<HTML>"}...],
   "tl":[{"s":시작초,"e":끝초,"i":섹션번호}...]}

사용:
  python3 pod_align.py ch52_full          # 특정 키
  python3 pod_align.py --all              # script.json 있는 것 전부
  python3 pod_align.py ch52_full --debug  # 세그먼트별 매핑 확인
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
NLM_DIR = BASE / "public" / "audio" / "nlm"
INTRO_DIR = BASE / "public" / "intros"

JUMP_PENALTY = 0.18   # 섹션 이동 페널티(비인접 이동은 거리 비례 가산)
MIN_SIM = 0.02        # 이 미만이면 어느 섹션과도 무관한 잡담으로 보고 직전 유지


def strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def bigrams(text: str) -> set:
    t = re.sub(r"[^0-9가-힣a-zA-Z]", "", text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def split_sections(intro_html: str) -> list[dict]:
    """<h4> 단위로 섹션 분리."""
    parts = [p for p in re.split(r"(?=<h4)", intro_html) if p.strip()]
    secs = []
    for p in parts:
        m = re.search(r"<h4[^>]*>(.*?)</h4>", p, re.S)
        title = strip_tags(m.group(1)) if m else "요약"
        secs.append({"t": title, "h": p, "text": strip_tags(p)})
    return secs


def align(segs: list[dict], secs: list[dict]) -> list[int]:
    """세그먼트별 섹션 인덱스(Viterbi)."""
    sec_bg = [bigrams(s["text"]) for s in secs]
    n, m = len(segs), len(secs)
    # 관측 점수: 자막 문장(직전 1문장 포함한 창) vs 섹션 바이그램 겹침
    # 다음 문장을 창에 넣으면 화제 전환 직전에 섹션이 미리 넘어가는 문제가 있어 뒤쪽은 보지 않는다
    obs = []
    for i in range(n):
        window = " ".join(segs[j]["t"] for j in range(max(0, i - 1), i + 1))
        wb = bigrams(window)
        row = []
        for k in range(m):
            inter = len(wb & sec_bg[k])
            row.append(inter / (len(wb) + 1))
        obs.append(row)
    # Viterbi: 유지 0, 인접 이동 JUMP_PENALTY, 먼 이동 거리 비례
    NEG = -1e9
    dp = [[NEG] * m for _ in range(n)]
    bk = [[0] * m for _ in range(n)]
    for k in range(m):
        dp[0][k] = obs[0][k] - (0 if k == 0 else JUMP_PENALTY * k * 0.3)
    for i in range(1, n):
        for k in range(m):
            best, arg = NEG, 0
            for p in range(m):
                pen = 0.0 if p == k else JUMP_PENALTY * (1 + 0.3 * (abs(k - p) - 1))
                if k < p:  # 뒤로 가는 이동은 추가 페널티(대체로 순방향 진행)
                    pen += 0.1
                v = dp[i - 1][p] - pen
                if v > best:
                    best, arg = v, p
            dp[i][k] = best + obs[i][k]
            bk[i][k] = arg
    path = [0] * n
    path[-1] = max(range(m), key=lambda k: dp[-1][k])
    for i in range(n - 1, 0, -1):
        path[i - 1] = bk[i][path[i]]
    return path


def make(key: str, debug: bool = False) -> Path | None:
    script = NLM_DIR / f"{key}.script.json"
    ch = key.split("_")[0]
    intro = INTRO_DIR / f"{ch}.json"
    if not script.exists() or not intro.exists():
        print(f"  건너뜀 {key}: script/intro 없음")
        return None
    segs = json.loads(script.read_text())
    secs = split_sections(json.loads(intro.read_text())["intro"])
    if not segs or not secs:
        print(f"  건너뜀 {key}: 데이터 비어 있음")
        return None
    path = align(segs, secs)
    if debug:
        for sg, k in zip(segs, path):
            print(f"  [{sg['s']:6.1f}] sec{k} {secs[k]['t'][:16]:18} | {sg['t'][:44]}")
    # 연속 같은 섹션 병합 → 타임라인
    tl = []
    for sg, k in zip(segs, path):
        if tl and tl[-1]["i"] == k:
            tl[-1]["e"] = sg["e"]
        else:
            tl.append({"s": sg["s"], "e": sg["e"], "i": k})
    out = NLM_DIR / f"{key}.sections.json"
    out.write_text(json.dumps(
        {"sections": [{"t": s["t"], "h": s["h"]} for s in secs], "tl": tl},
        ensure_ascii=False))
    used = sorted({x["i"] for x in tl})
    print(f"  {key}: 세그 {len(segs)} → 구간 {len(tl)}, 사용 섹션 {used}/{len(secs)}개 중")
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    debug = "--debug" in sys.argv
    if "--all" in sys.argv:
        keys = [p.stem.replace(".script", "")
                for p in NLM_DIR.glob("*.script.json")]
    elif args:
        keys = args
    else:
        print(__doc__)
        return
    for k in keys:
        make(k, debug)


if __name__ == "__main__":
    main()
