#!/usr/bin/env python3
"""intros/*.json 의 intro/introEasy HTML을 세그먼트로 나눠 구글 TTS로 MP3 미리 생성.
같은 도메인 정적 파일 → iOS 사파리에서 100% 재생.
세그먼트 추출은 app.html 의 querySelectorAll("h4,p,li,.tip") 순서를 복제."""
import os, re, json, time, html, hashlib, sys, asyncio
import edge_tts
from html.parser import HTMLParser

VOICE = "ko-KR-SunHiNeural"   # 마이크로소프트 자연스러운 뉴럴 여성 음성

PUB = os.path.join(os.path.dirname(__file__), "public")
INTRO_DIR = os.path.join(PUB, "intros")
AUDIO_DIR = os.path.join(PUB, "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

CAP_TAGS = {"h4", "p", "li"}

class SegParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []      # active capture frames: list of char-lists
        self.segs = []
    def _is_cap(self, tag, attrs):
        if tag in CAP_TAGS:
            return True
        cls = dict(attrs).get("class", "") or ""
        return "tip" in cls.split()
    def handle_starttag(self, tag, attrs):
        if self._is_cap(tag, attrs):
            self.stack.append({"tag": tag, "buf": []})
    def handle_startendtag(self, tag, attrs):
        pass
    def handle_data(self, data):
        for f in self.stack:
            f["buf"].append(data)
    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                frame = self.stack.pop(i)
                self.segs.append("".join(frame["buf"]))
                break

EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2190-\u21FF]")
def clean(t):
    t = html.unescape(t or "")
    t = EMOJI.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()

def extract_segments(html_str):
    p = SegParser()
    p.feed(html_str)
    return [clean(s) for s in p.segs if clean(s)]

def chunks(t, mx=190):
    parts = re.split(r"(?<=[.!?。…])\s+", t)
    out, buf = [], ""
    for pp in parts:
        cand = (buf + " " + pp).strip() if buf else pp
        if len(cand) <= mx:
            buf = cand
        else:
            if buf:
                out.append(buf); buf = ""
            if len(pp) <= mx:
                buf = pp
            else:
                for i in range(0, len(pp), mx):
                    out.append(pp[i:i+mx])
    if buf:
        out.append(buf)
    return [c for c in out if c.strip()]

async def _edge(text):
    c = edge_tts.Communicate(text, VOICE)
    data = b""
    async for chunk in c.stream():
        if chunk["type"] == "audio":
            data += chunk["data"]
    return data

def seg_audio(text):
    # edge-tts는 긴 문장도 한번에 자연스럽게 합성 → 청크 분할 불필요
    for attempt in range(5):
        try:
            data = asyncio.run(_edge(text))
            if data:
                return data
        except Exception as e:
            wait = 1.5 * (attempt + 1)
            print(f"    retry {attempt+1} ({e}) wait {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError("edge-tts failed: " + text[:30])

def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None   # 특정 챕터만: ch27
    manifest = {}
    mfile = os.path.join(AUDIO_DIR, "manifest.json")
    if os.path.exists(mfile):
        manifest = json.load(open(mfile))
    files = sorted(f for f in os.listdir(INTRO_DIR) if f.endswith(".json"))
    for fn in files:
        ch = fn[:-5]
        if only and ch != only:
            continue
        j = json.load(open(os.path.join(INTRO_DIR, fn)))
        entry = manifest.get(ch, {})
        for mode, key in (("full", "intro"), ("easy", "introEasy")):
            htmls = j.get(key) or ""
            if not htmls.strip():
                entry.pop(mode, None); entry.pop(mode + "_h", None)
                continue
            h = hashlib.md5(htmls.encode()).hexdigest()[:10]
            if entry.get(mode + "_h") == h and not only:
                print(f"skip {ch}/{mode} (unchanged)")
                continue
            segs = extract_segments(htmls)
            print(f"{ch}/{mode}: {len(segs)} segments", flush=True)
            for i, s in enumerate(segs):
                out = os.path.join(AUDIO_DIR, f"{ch}_{mode}_{i}.mp3")
                audio = seg_audio(s)
                with open(out, "wb") as w:
                    w.write(audio)
                print(f"   [{i}] {len(audio)}B  {s[:28]}", flush=True)
            # 남은 옛 파일 정리
            k = len(segs)
            while os.path.exists(os.path.join(AUDIO_DIR, f"{ch}_{mode}_{k}.mp3")):
                os.remove(os.path.join(AUDIO_DIR, f"{ch}_{mode}_{k}.mp3")); k += 1
            entry[mode] = len(segs)
            entry[mode + "_h"] = h
        manifest[ch] = entry
        json.dump(manifest, open(mfile, "w"), ensure_ascii=False, indent=0)
    print("DONE. manifest:", mfile)

if __name__ == "__main__":
    main()
