"""Сбор и дозапись сырых данных из HTML-каталога (books.toscrape.com) и Open Library REST API.

Используется только для первичного сбора или добора новых порций (режим append-only).
Пересборка clean/ и mart/ выполняется из уже сохранённого слоя raw/ через src/rebuild.py.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "raw"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Educational-DataCollector/2.0; Variant-2-BooksAuthors)"
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def collect_books_toscrape(pages: int = 2, delay: float = 0.5) -> list[dict]:
    """Парсинг каталога books.toscrape.com с сохранением сырых строковых полей."""
    session = requests.Session()
    session.headers.update(HEADERS)
    collected: list[dict] = []

    for page in range(1, pages + 1):
        url = f"https://books.toscrape.com/catalogue/page-{page}.html"
        resp = session.get(url, timeout=20)
        resp.encoding = "utf-8"
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("article.product_pod"):
            a_tag = card.select_one("h3 a")
            href = a_tag["href"]
            detail_url = urljoin(url, href)
            book_id = detail_url.rstrip("/").split("/")[-2].split("_")[-1]
            collected.append({
                "book_id_raw": book_id,
                "title_raw": a_tag["title"],
                "category_raw": "Books",
                "price_raw": card.select_one(".price_color").get_text(strip=True),
                "availability_raw": card.select_one(".instock.availability").get_text(" ", strip=True),
                "rating_raw": " ".join(card.select_one("p.star-rating")["class"]),
                "upc_raw": f"toscrape_{book_id}",
                "detail_url_raw": detail_url,
                "_source": "books_toscrape_html",
                "_source_url": url,
                "_collected_at": now_utc_iso(),
                "_parser_version": "books-bs4/2.0",
                "_status": resp.status_code,
                "_is_synthetic_dirty": False,
            })
        time.sleep(delay)

    return collected


def collect_openlibrary_api(queries: list[str] | None = None, limit_per_query: int = 25, delay: float = 0.5) -> list[dict]:
    """Сбор книг и обогащение профилей авторов через Open Library REST API."""
    if queries is None:
        queries = ["subject:fiction", "subject:science_fiction", "subject:history", "subject:mystery"]

    session = requests.Session()
    session.headers.update(HEADERS)
    author_cache: dict[str, dict] = {}
    collected: list[dict] = []

    for q in queries:
        api_url = f"https://openlibrary.org/search.json?q={q}&limit={limit_per_query}"
        resp = session.get(api_url, timeout=25)
        resp.raise_for_status()
        docs = resp.json().get("docs", [])

        for doc in docs:
            work_key = (doc.get("key") or "").split("/")[-1]
            author_keys = doc.get("author_key") or []
            author_names = doc.get("author_name") or []
            if not work_key or not author_keys:
                continue

            akey = author_keys[0]
            if akey not in author_cache:
                a_resp = session.get(f"https://openlibrary.org/authors/{akey}.json", timeout=20)
                if a_resp.status_code == 200:
                    a_json = a_resp.json()
                    birth = str(a_json.get("birth_date") or "")[:4]
                    author_cache[akey] = {
                        "birth_year": birth if birth.isdigit() else None,
                        "top_work": a_json.get("top_work") or doc.get("title"),
                        "work_count": a_json.get("work_count") or 1,
                    }
                else:
                    author_cache[akey] = {"birth_year": None, "top_work": doc.get("title"), "work_count": 1}
                time.sleep(0.2)

            ainfo = author_cache[akey]
            isbns = doc.get("isbn") or []
            subjects = doc.get("subject") or [q.replace("subject:", "").title()]

            collected.append({
                "work_key": work_key,
                "isbn_raw": isbns[0] if isbns else None,
                "title_raw": doc.get("title"),
                "category_raw": subjects[0] if subjects else None,
                "author_key_raw": akey,
                "author_name_raw": author_names[0] if author_names else "Unknown",
                "author_birth_date_raw": ainfo["birth_year"],
                "author_top_work_raw": ainfo["top_work"],
                "author_work_count_raw": ainfo["work_count"],
                "first_publish_year_raw": doc.get("first_publish_year"),
                "ratings_average_raw": doc.get("ratings_average"),
                "edition_count_raw": doc.get("edition_count"),
                "price_raw": None,
                "availability_raw": None,
                "detail_url_raw": f"https://openlibrary.org/works/{work_key}",
                "_source": "openlibrary_api",
                "_source_url": api_url,
                "_collected_at": now_utc_iso(),
                "_parser_version": "openlibrary-api/1.0",
                "_status": resp.status_code,
                "_is_synthetic_dirty": False,
            })
        time.sleep(delay)

    return collected


def main() -> None:
    parser = argparse.ArgumentParser(description="Append-only сборщик данных в raw/")
    parser.add_argument("--pages", type=int, default=2, help="Число страниц books.toscrape.com")
    parser.add_argument("--limit", type=int, default=25, help="Лимит на запрос Open Library API")
    args = parser.parse_args()

    html_rows = collect_books_toscrape(pages=args.pages)
    append_jsonl(RAW_DIR / "books_toscrape.jsonl", html_rows)
    print(f"Дозаписано HTML-записей: {len(html_rows)}")

    api_rows = collect_openlibrary_api(limit_per_query=args.limit)
    append_jsonl(RAW_DIR / "openlibrary_api.jsonl", api_rows)
    print(f"Дозаписано API-записей: {len(api_rows)}")


if __name__ == "__main__":
    main()
