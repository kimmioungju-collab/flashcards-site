"""committees_book 낭독문 변환 규칙 검사. 실행: python3 tests/committees_book_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import committees_book as book  # noqa: E402

CASES = [
    # (종류, 대본 한 줄, 낭독문)
    ("h3", "(1) 설치 (법 제4조) 〔☆ 15년 소방교〕", "설치. 법 제4조."),
    ("h2", "2 인사위원회의 구성 및 운영 〔☆ 15년 소방교〕", "인사위원회의 구성 및 운영."),
    ("h3", "(2) 2인 이상 소방공무원 관련사건의 관할 (제3조)", "2인 이상 소방공무원 관련사건의 관할. 제3조."),
    ("h3", "(4) 채용비위심의위원회 (임용령 제51조의3, 신설 2024.8.13.)", "채용비위심의위원회. 임용령 제51조의3, 신설 2024년 8월 13일."),
    ("n1", "② 위원 : 소속 소방정 이상의 소방공무원 중에서 임명한다.〔제2항〕", "둘째, 위원. 소속 소방정 이상의 소방공무원 중에서 임명한다."),
    ("n2", "㉡ 시·도 : 소방본부장", "둘, 시도. 소방본부장."),
    ("n3", "가. 법관·검사 또는 변호사의 직에 10년 이상 근무한 사람", "가, 법관 검사 또는 변호사의 직에 10년 이상 근무한 사람."),
    ("n2", "3. 제74조제4항에 따른 사항", "셋, 제74조제4항에 따른 사항."),
    ("p", "시·도(특별시·광역시·특별자치시·도·특별자치도)에 인사위원회를 둔다.", "시도, 특별시, 광역시, 특별자치시, 도, 특별자치도에 인사위원회를 둔다."),
    ("p", "소방공무원(고위공무원단에 속하는 공무원을 포함한다)으로서 소방청장이 지명하는 사람",
     "소방공무원으로서, 고위공무원단에 속하는 공무원을 포함한다, 소방청장이 지명하는 사람."),
    ("n1", "② 소방청 보통승진심사위원회 : 승진심사(④ 및 ⑤의 승진심사위원회에서 관할하는 경우는 제외한다)",
     "둘째, 소방청 보통승진심사위원회. 승진심사, 4번 및 5번의 승진심사위원회에서 관할하는 경우는 제외한다."),
    ("p", "중앙소방학교·중앙119구조본부 및 국립소방연구원", "중앙소방학교, 중앙일일구구조본부 및 국립소방연구원."),
    ("p", "추인은 {찬·반투표|찬반투표}로써 한다.", "추인은 찬반투표로써 한다."),
    ("p", "당해 승진심사기간 중에는 2이상의 계급의 승진심사위원을 겸할 수 없다.", "당해 승진심사기간 중에는 2 이상의 계급의 승진심사위원을 겸할 수 없다."),
    ("box", "〔교재 26쪽 위원회 비교표〕{|교재 위원회 비교표의 내용.} 의결 : 위원 5명 이상의 출석", "교재 위원회 비교표의 내용. 의결. 위원 5명 이상의 출석."),
]


def test_speak() -> None:
    for kind, line, want in CASES:
        got = book.speak(kind, line)
        assert got == want, f"\n  줄: {line}\n  기대: {want}\n  결과: {got}"


def test_classify() -> None:
    assert book.classify("### (1) 설치")[0] == "h3"
    assert book.classify("> 예제 : 본문")[0] == "box"
    assert book.classify("① 설치 : 둔다")[0] == "n1"
    assert book.classify("ⓐ 소방공무원으로")[0] == "n3"
    assert book.classify("소방공무원인사위원회는")[0] == "p"


def test_script_parses() -> None:
    tracks = book.parse()
    assert [t["no"] for t in tracks] == [f"{i:02d}" for i in range(1, len(tracks) + 1)], "트랙 번호가 순서대로가 아님"
    for t in tracks:
        for it in t["items"]:
            assert it["sp"].strip(" ."), f"빈 낭독문: {t['no']} {it['d']}"
            assert not set("〔〕{}|()·「」") & set(it["sp"]), f"기호가 낭독문에 남음: {t['no']} {it['sp'][:60]}"
            assert "{" not in it["d"], f"화면 글에 지정 표기가 남음: {it['d'][:60]}"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"통과: {name}")
