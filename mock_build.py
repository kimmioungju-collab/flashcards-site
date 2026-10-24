#!/usr/bin/env python3
"""모의고사 채점 결과(/tmp/mock5_*.json 등) → public/data/mock/rNN.json + index.json 병합.

사용: python3 mock_build.py <회차> <날짜 YYYY-MM-DD> <과목JSON>...
예:   python3 mock_build.py 5 2026-10-24 /tmp/mock5_admin.json /tmp/mock5_tactic.json
각 과목 JSON: {"subject":"admin","name":"행정법","round":5,"items":[{no,type,q,opts,my,ans,wrong,confidence,exp,basis,src}]}
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "public" / "data" / "mock"
SUBJECT_ORDER = ["admin", "fire", "tactic", "hazmat"]
REQUIRED = ("no", "q", "opts", "my", "ans", "exp")


def load_subject(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("subject", "name", "items"):
        if key not in data:
            raise ValueError(f"{path}: '{key}' 누락")
    items = []
    for it in data["items"]:
        missing = [k for k in REQUIRED if k not in it]
        if missing:
            raise ValueError(f"{path} 문{it.get('no')}: {missing} 누락")
        my, ans = str(it["my"]).strip(), str(it["ans"]).strip()
        items.append({**it, "my": my, "ans": ans, "wrong": my != ans})
    items.sort(key=lambda x: int(str(x["no"]).strip("문. ")))
    return {**data, "items": items}


def build(round_no: int, date: str, subject_paths: list[str]) -> Path:
    subjects = {d["subject"]: {"name": d["name"], "items": d["items"]} for d in map(load_subject, subject_paths)}
    ordered = {k: subjects[k] for k in SUBJECT_ORDER if k in subjects}
    ordered.update({k: v for k, v in subjects.items() if k not in ordered})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"r{round_no:02d}.json"
    out.write_text(json.dumps({"round": round_no, "date": date, "subjects": ordered}, ensure_ascii=False, indent=1), encoding="utf-8")
    update_index(round_no, date, ordered)
    return out


def update_index(round_no: int, date: str, subjects: dict) -> None:
    idx_path = OUT_DIR / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {"rounds": []}
    entry = {"round": round_no, "date": date,
             "subjects": {k: {"name": v["name"], "total": len(v["items"]),
                              "wrong": sum(1 for i in v["items"] if i["wrong"])} for k, v in subjects.items()}}
    rounds = [r for r in idx["rounds"] if r["round"] != round_no] + [entry]
    idx_path.write_text(json.dumps({"rounds": sorted(rounds, key=lambda r: r["round"])}, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    out = build(int(sys.argv[1]), sys.argv[2], sys.argv[3:])
    data = json.loads(out.read_text(encoding="utf-8"))
    for k, v in data["subjects"].items():
        wrong = [i["no"] for i in v["items"] if i["wrong"]]
        print(f"{v['name']}({k}): {len(v['items'])}문항, 오답 {len(wrong)} → {wrong}")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
