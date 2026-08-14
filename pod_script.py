#!/usr/bin/env python3
"""NotebookLM 팟캐스트 mp3 → 타임스탬프 스크립트(.script.json) 생성.

Whisper(small)로 전사해 [{"s":시작초,"e":끝초,"t":"문장"}] 형식으로
public/audio/nlm/<key>.script.json 에 저장한다. 앱이 재생 위치에 맞춰
해당 문장을 하이라이트하는 데 쓴다.

사용:
  python3 pod_script.py ch53_full        # 특정 키
  python3 pod_script.py --all            # 스크립트 없는 mp3 전부
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
NLM_DIR = BASE / "public" / "audio" / "nlm"
WHISPER = "/opt/homebrew/bin/whisper"
MODEL = "small"


def transcribe(mp3: Path) -> list[dict]:
    """whisper CLI로 전사해 세그먼트 리스트를 돌려준다."""
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            [WHISPER, str(mp3), "--model", MODEL, "--language", "ko",
             "--output_format", "json", "--output_dir", td, "--fp16", "False"],
            capture_output=True, text=True, timeout=3600,
        )
        out = Path(td) / (mp3.stem + ".json")
        if r.returncode != 0 or not out.exists():
            raise RuntimeError("whisper 실패: " + (r.stderr or r.stdout)[-200:])
        data = json.loads(out.read_text())
    segs = []
    for s in data.get("segments", []):
        txt = (s.get("text") or "").strip()
        if not txt:
            continue
        segs.append({"s": round(float(s["start"]), 1),
                     "e": round(float(s["end"]), 1), "t": txt})
    return segs


def make(key: str) -> Path:
    mp3 = NLM_DIR / f"{key}.mp3"
    if not mp3.exists():
        raise FileNotFoundError(f"{mp3} 없음")
    segs = transcribe(mp3)
    if not segs:
        raise RuntimeError(f"{key}: 전사 결과 비어 있음")
    dst = NLM_DIR / f"{key}.script.json"
    dst.write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    print(f"완료 {key}: {len(segs)}문장 → {dst.name}", flush=True)
    return dst


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "--all":
        keys = [p.stem for p in sorted(NLM_DIR.glob("*.mp3"))
                if not (NLM_DIR / f"{p.stem}.script.json").exists()]
    else:
        keys = sys.argv[1:]
    for key in keys:
        try:
            make(key)
        except Exception as e:  # 한 건 실패해도 나머지는 계속
            print(f"실패 {key}: {e}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
