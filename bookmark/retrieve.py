"""교재 쪽 검색 — 문자 n-gram TF-IDF."""
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from common import MIN_PAGE_TEXT, load_pages

WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    return WS.sub(" ", s).strip()


class PageIndex:
    def __init__(self, books: set[str] | None = None):
        pages = load_pages()
        self.ids = [k for k, v in pages.items()
                    if v["nlen"] >= MIN_PAGE_TEXT and (books is None or v["book"] in books)]
        self.pages = pages
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), sublinear_tf=True, min_df=2)
        self.mat = self.vec.fit_transform(_clean(pages[k]["text"]) for k in self.ids)

    def search(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        q = self.vec.transform([_clean(query)])
        scores = linear_kernel(q, self.mat)[0]
        top = scores.argsort()[::-1][:k]
        return [(self.ids[i], float(scores[i])) for i in top]
