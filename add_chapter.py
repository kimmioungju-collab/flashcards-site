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

    return True

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
