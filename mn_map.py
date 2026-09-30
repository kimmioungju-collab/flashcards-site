#!/usr/bin/env python3
"""두문자 ↔ 문항 매핑 생성: data/mnemonics.json 의 각 두문자(ch 필드)를 해당 챕터 문항에 대응.
claude(Fable 5.1)가 챕터별로 '이 문항이 이 두문자가 묶어둔 항목/규칙을 직접 묻는가' 판정 → public/data/mn_map.json
  {"updated": "...", "map": {"ch39": {"01": ["seonggong"], ...}, ...}}
사용: python3 mn_map.py [ch39 ch41 ...]   (인자 없으면 두문자가 걸린 전 챕터, 기존 결과는 유지·갱신)
"""
import json, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MN_FILE = ROOT / "public/data/mnemonics.json"
OUT_FILE = ROOT / "public/data/mn_map.json"
CH_DIR = ROOT / "public/chapters"
CLAUDE = "/opt/homebrew/bin/claude"
MODEL = "claude-fable-5-1"
TIMEOUT = 600


def claude(prompt: str) -> str:
    r = subprocess.run([CLAUDE, "-p", "--model", MODEL, "--output-format", "text", "--tools", ""],
                       input=prompt, capture_output=True, text=True, timeout=TIMEOUT, cwd="/tmp")
    if r.returncode != 0 and not (r.stdout and r.stdout.strip()):
        raise RuntimeError(f"claude 실패: {(r.stderr or r.stdout)[-300:]}")
    return r.stdout


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSON 없음: " + text[:200])
    return json.loads(m.group(0), strict=False)


def mn_block(items: list[dict]) -> str:
    out = []
    for it in items:
        rows = " / ".join(f"{x['k']}={x['v']}" for x in it["items"])
        out.append(f"[id={it['id']}] 「{it['mn']}」 — {it['what']}\n   글자풀이: {rows}\n   의미: {it['meaning']}")
    return "\n".join(out)


def q_block(qs: list[dict]) -> str:
    return "\n".join(f"[{q['no']}] ({q.get('ans','')}) {q['q']}\n   해설: {q.get('exp','')}" for q in qs)


def map_chapter(ch: str, mns: list[dict], qs: list[dict]) -> dict:
    ids = {it["id"] for it in mns}
    prompt = f"""당신은 행정법 수험 교재 편집자입니다.
아래 [두문자]는 교재·강의에서 쓰는 암기법이고, [문제]는 같은 챕터의 OX·객관식 문항입니다.
문항별로, 그 문항의 정답을 가르는 쟁점이 어떤 두문자가 묶어둔 **항목(글자풀이) 또는 규칙 자체**를 직접 묻는지 판정하세요.

판정 기준(엄격):
- 두문자의 글자풀이 항목 중 하나를 문항이 직접 다루거나(예: '○○는 통치행위인가'), 두문자가 요약한 규칙(예: 사전통지 생략 사유)의 적용을 묻는 경우에만 연결.
- 같은 챕터라는 이유만으로, 또는 주제가 인접하다는 이유로 연결하지 말 것. 두문자를 떠올려도 정답 판단에 도움이 안 되면 연결 X.
- 한 문항에 여러 두문자가 해당되면 모두 나열. 해당 없는 문항은 목록에서 빼세요.

[두문자]
{mn_block(mns)}

[문제]
{q_block(qs)}

출력은 아래 JSON 하나만 (설명 금지):
{{"map": {{"문항번호": ["두문자id", ...], ...}}}}"""
    res = parse_json(claude(prompt)).get("map", {})
    valid_no = {q["no"] for q in qs}
    clean = {}
    for no, lst in res.items():
        no = str(no).zfill(2)
        keep = [i for i in (lst or []) if i in ids]
        if no in valid_no and keep:
            clean[no] = keep
    return clean


def main():
    mn = json.load(open(MN_FILE, encoding="utf-8"))["items"]
    by_ch: dict[str, list[dict]] = {}
    for it in mn:
        for ch in it.get("ch", []):
            by_ch.setdefault(ch, []).append(it)
    targets = sys.argv[1:] or sorted(by_ch)
    out = {"updated": "", "map": {}}
    if OUT_FILE.exists():
        out = json.load(open(OUT_FILE, encoding="utf-8"))
    for ch in targets:
        f = CH_DIR / f"{ch}.json"
        if ch not in by_ch or not f.exists():
            print(f"[{ch}] 건너뜀 (두문자 없음 또는 챕터 파일 없음)"); continue
        qs = json.load(open(f, encoding="utf-8"))["questions"]
        try:
            m = map_chapter(ch, by_ch[ch], qs)
        except Exception as e:  # noqa: BLE001
            print(f"[{ch}] 실패: {e}"); continue
        out["map"][ch] = m
        out["updated"] = time.strftime("%Y-%m-%d %H:%M")
        OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{ch}] {len(m)}/{len(qs)}문항 연결 → {sorted({i for l in m.values() for i in l})}")


if __name__ == "__main__":
    main()
