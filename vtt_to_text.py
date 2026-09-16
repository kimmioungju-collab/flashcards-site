#!/usr/bin/env python3
"""유튜브 자동자막 vtt → 중복 제거된 평문 텍스트.

사용: python3 vtt_to_text.py <in.vtt> <out.txt>
"""
import re
import sys
from pathlib import Path


def convert(vtt_path: str) -> str:
    lines_out: list[str] = []
    prev = ""
    for raw in Path(vtt_path).read_text(errors="ignore").splitlines():
        line = re.sub(r"<[^>]+>", "", raw).strip()      # 인라인 타임태그 제거
        if (not line or "-->" in line or line.isdigit()
                or line.startswith(("WEBVTT", "Kind:", "Language:", "align:", "position:"))):
            continue
        if line == prev:                                # 자동자막 특유의 반복 줄 제거
            continue
        lines_out.append(line)
        prev = line
    return "\n".join(lines_out)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    out = convert(sys.argv[1])
    Path(sys.argv[2]).write_text(out)
    print(f"{sys.argv[2]}: {len(out)}자")
