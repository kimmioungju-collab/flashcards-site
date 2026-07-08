# 중국어 단어 암기 플래시카드

챕터별 JSON 파일로 구성된 중국어 단어 암기 PWA 웹앱입니다.

## 🚀 빠른 시작

### 1. Firebase 프로젝트 연결

```bash
# Firebase 콘솔에서 프로젝트 생성 후
firebase use --add
# 또는 기존 프로젝트 사용
firebase use <project-id>
```

### 2. 첫 배포

```bash
firebase deploy --only hosting
```

## 📚 챕터 추가 방법

### 방법 1: 로컬에서 직접 추가

```bash
python3 add_chapter.py <챕터파일.json> <챕터번호>

# 예시
python3 add_chapter.py new_chapter.json 02
```

### 방법 2: 텔레그램으로 자동 배포

1. 새 챕터 JSON을 GitHub에 push
2. 텔레그램으로 메시지 보내기:
   ```
   새 챕터 배포해줘
   ```

챕터 JSON 형식:

```json
{
  "chapter": 1,
  "title": "챕터 1 - 기초 인사",
  "words": [
    {
      "chinese": "你好",
      "pinyin": "nǐ hǎo",
      "korean": "안녕하세요"
    }
  ]
}
```

## 🎯 프로젝트 구조

```
flashcards-site/
├── app.html              # 메인 PWA 앱
├── manifest.json         # PWA 매니페스트
├── chapters/             # 챕터 JSON 파일들
│   ├── ch01.json
│   ├── ch02.json
│   └── ...
├── chapters-list.json    # 챕터 목록 (자동 생성)
├── add_chapter.py        # 챕터 추가 스크립트
├── firebase.json         # Firebase 설정
└── .firebaserc           # Firebase 프로젝트 설정
```

## 📱 사용 방법

1. 웹앱 접속
2. 챕터 선택
3. 카드 클릭해서 뒤집기
4. 좌우 스와이프 또는 버튼으로 넘기기

## ⌨️ 키보드 단축키

- `←` / `→`: 이전/다음 카드
- `Space`: 카드 뒤집기

## 🔧 텔레그램 봇 자동화

텔레그램으로 다음 명령 중 하나를 보내면 자동으로 처리됩니다:

- "새 챕터 배포해줘"
- "챕터 배포"
- "flashcard 배포"

봇이 자동으로:
1. GitHub에서 git pull
2. 최신 챕터 확인
3. Firebase 배포
4. 결과 알림

## 📄 라이선스

MIT
