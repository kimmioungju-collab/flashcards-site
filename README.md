# 행정법 OX/객관식 플래시카드

ox-flashcard-html 스킬 기반의 행정법 문제 플래시카드 웹앱입니다.

## 🚀 빠른 시작

### 1. Firebase 배포

```bash
firebase deploy --only hosting
```

## 📚 챕터 추가 방법

### 방법 1: 로컬에서 직접 추가

```bash
python3 add_chapter.py <questions.json> <챕터번호>

# 예시
python3 add_chapter.py questions.json 06
```

### 방법 2: 텔레그램으로 자동 배포

텔레그램으로 메시지:
```
새 챕터 배포해줘
```

챕터 JSON 형식 (ox-study-excel 스킬과 동일):

```json
{
  "title": "챕터 06 - 행정법 기초",
  "questions": [
    {
      "no": "01",
      "grade": "B",
      "theme": "행정법의 의의",
      "q": "행정법이란 행정에 관한 국내 공법을 말한다.",
      "ans": "O",
      "src": "기출",
      "exp": "행정법은 행정에 관한 국내 공법이다..."
    }
  ]
}
```

**해설 작성 규칙:**
- 정답이 X인 문제: `[틀린 부분] '문제 속 틀린 문구' → 올바른 내용`
- 정답이 O인 문제: 근거 조문·판례 요지
- 객관식: `q`에 보기를 줄바꿈으로 구분 (`①...\n②...\n③...`), 해설은 `정답 ③: ...` 형식

## 🎯 프로젝트 구조

```
flashcards-site/
├── public/
│   ├── index.html         # 목차 페이지
│   ├── app.html           # 플래시카드 앱
│   └── chapters/          # 챕터 JSON 파일들
│       ├── ch06.json
│       ├── ch07.json
│       └── ...
├── add_chapter.py         # 챕터 추가 스크립트
├── firebase.json          # Firebase 설정
└── .firebaserc            # Firebase 프로젝트 설정
```

## 📱 앱 기능

- **실전 모드**: 문제 → O/X(객관식은 ①~⑤) → 채점 + 정답·해설
- **학습 모드**: 핵심 개념 → 문제 → 채점 (정답 스포일러 방지)
- 등급 필터 (S/A/B/C/기/인/소/Z), 유형 필터, 순서 섞기
- 오답만 재도전, 취약 문제 필터
- 문항별 누적 통계, 회차별 기록
- localStorage 학습 이력 (이어하기/새로 시작)

## ⌨️ 키보드 단축키

- `1` = O, `2` = X (객관식은 `1~5` = ①~⑤)
- `←` = 이전 카드, `→` = 다음/문제 풀기
- `Space`/`Enter` = 진행

## 🔧 텔레그램 봇 자동화

텔레그램: `새 챕터 배포해줘` → 자동으로 git pull + Firebase 배포

## 📄 라이선스

MIT
