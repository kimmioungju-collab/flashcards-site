#!/usr/bin/env python3
"""
새 챕터를 추가하고 Firebase에 배포하는 스크립트

사용법:
  python3 add_chapter.py <chapter_json_file> <chapter_number>

예시:
  python3 add_chapter.py new_chapter.json 02
  python3 add_chapter.py ch03.json 03
"""

import sys
import json
import shutil
from pathlib import Path
import subprocess

def add_chapter(json_file, chapter_num):
    """새 챕터 추가"""
    project_root = Path(__file__).parent
    chapters_dir = project_root / "public" / "chapters"

    # 챕터 번호 포맷 (01, 02, ...)
    ch_num = str(chapter_num).zfill(2)
    target_file = chapters_dir / f"ch{ch_num}.json"

    # JSON 파일 복사
    source_file = Path(json_file)
    if not source_file.exists():
        print(f"❌ 파일을 찾을 수 없습니다: {json_file}")
        return False

    # JSON 유효성 검사
    try:
        with open(source_file, 'r', encoding='utf-8') as f:
            chapter_data = json.load(f)

        if 'questions' not in chapter_data:
            print("❌ JSON에 'questions' 필드가 없습니다")
            return False

        question_count = len(chapter_data['questions'])
        title = chapter_data.get('title', f'챕터 {ch_num}')

        print(f"✅ JSON 유효성 확인: {question_count}개 문제")

    except json.JSONDecodeError as e:
        print(f"❌ JSON 파싱 오류: {e}")
        return False

    # 챕터 파일 복사
    shutil.copy(source_file, target_file)
    print(f"✅ 챕터 파일 복사: {target_file}")
    print(f"📊 챕터 {ch_num}: {title} ({question_count}문제)")

    # manifest 갱신 (문항수 + 등급별 카운트)
    update_manifest(project_root, f"ch{ch_num}", title, chapter_data['questions'])

    return True

GRADE_ORDER = ["S", "A", "B", "C", "기", "인", "소", "Z", "-"]

def grade_counts(questions):
    """등급별 문항수 집계 (등급 순서대로 정렬)"""
    counts = {}
    for q in questions:
        g = (q.get("grade") or "").strip() or "-"
        counts[g] = counts.get(g, 0) + 1
    ordered = {g: counts[g] for g in GRADE_ORDER if counts.get(g)}
    for g, v in counts.items():
        if g not in ordered:
            ordered[g] = v
    return ordered

def update_manifest(project_root, chapter_id, title, questions):
    """manifest.json에 챕터 정보(문항수·등급 카운트) upsert"""
    mpath = project_root / "public" / "data" / "manifest.json"
    try:
        with open(mpath, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {"chapters": []}

    entry = {"id": chapter_id, "title": title,
             "count": len(questions), "grades": grade_counts(questions)}
    chapters = manifest.setdefault("chapters", [])
    for i, ch in enumerate(chapters):
        if ch.get("id") == chapter_id:
            # 기존 제목 유지(있으면), 나머지 갱신
            entry["title"] = ch.get("title", title)
            chapters[i] = entry
            break
    else:
        chapters.append(entry)
        chapters.sort(key=lambda c: c.get("id", ""))

    with open(mpath, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"✅ manifest 갱신: {chapter_id} 등급 {grade_counts(questions)}")

def deploy_firebase():
    """Firebase에 배포"""
    print("\n🚀 Firebase 배포 시작...")
    try:
        result = subprocess.run(['firebase', 'deploy', '--only', 'hosting'],
                              capture_output=True, text=True, check=True)
        print("✅ Firebase 배포 완료!")
        print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Firebase 배포 실패: {e}")
        print(e.stderr)
        return False
    except FileNotFoundError:
        print("❌ Firebase CLI가 설치되어 있지 않습니다.")
        print("   설치: npm install -g firebase-tools")
        return False

def main():
    if len(sys.argv) < 3:
        print("사용법: python3 add_chapter.py <chapter_json_file> <chapter_number>")
        print("예시: python3 add_chapter.py new_chapter.json 02")
        sys.exit(1)

    json_file = sys.argv[1]
    chapter_num = sys.argv[2]

    print(f"📚 새 챕터 추가: ch{chapter_num}")
    print(f"📄 소스 파일: {json_file}\n")

    if not add_chapter(json_file, chapter_num):
        print("\n❌ 챕터 추가 실패")
        sys.exit(1)

    # Firebase 배포
    if deploy_firebase():
        print("\n🎉 모든 작업 완료!")
        print(f"💡 웹앱에서 '챕터 {chapter_num}'를 확인하세요.")
    else:
        print("\n⚠️  챕터는 추가되었지만 배포에 실패했습니다.")
        print("   수동으로 배포하려면: firebase deploy --only hosting")
        sys.exit(1)

if __name__ == '__main__':
    main()
