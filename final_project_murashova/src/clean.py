import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
from pydantic import ValidationError

from src.collect_api import normalize_title
from src.contract import BookRecord, OpenLibraryRecord
from src.observability import utc_now_iso

RATING_MAP = {
    "One": 1,
    "Two": 2,
    "Three": 3,
    "Four": 4,
    "Five": 5,
}


def parse_price(price_raw: str) -> float:
    match = re.search(r"(\d+\.\d+|\d+)", price_raw.replace(",", "."))
    if not match:
        raise ValueError(f"Cannot parse price from '{price_raw}'")
    return float(match.group(1))


def make_book_key(product_url: str) -> str:
    return hashlib.sha256(product_url.strip().encode("utf-8")).hexdigest()[:24]


def clean_books(
    raw_path: Path, clean_dir: Path, parser_version: str
) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    clean_dir.mkdir(parents=True, exist_ok=True)
    rejects: List[Dict[str, Any]] = []
    latest_by_key: Dict[str, Dict[str, Any]] = {}

    if not raw_path.exists():
        return pd.DataFrame(), rejects

    now_iso = utc_now_iso()
    with raw_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw_obj = json.loads(line)
                payload = raw_obj.get("payload", {})
                title = (payload.get("title") or "").strip()
                product_url = (payload.get("product_url") or "").strip()
                price_gbp = parse_price(payload.get("price_raw", ""))
                rating = RATING_MAP.get(payload.get("rating_word", ""), 0)
                avail_text = (payload.get("availability_raw") or "").strip()
                in_stock = 1 if "in stock" in avail_text.lower() else 0

                book = BookRecord(
                    book_key=make_book_key(product_url),
                    title=title,
                    title_norm=normalize_title(title),
                    category=(payload.get("category") or "").strip(),
                    price_gbp=price_gbp,
                    rating=rating,
                    in_stock=in_stock,
                    availability_text=avail_text,
                    product_url=product_url,
                    source=raw_obj.get("source", "books.toscrape.com"),
                    collected_at=raw_obj.get("collected_at", now_iso),
                    updated_at=now_iso,
                    parser_version=raw_obj.get("parser_version", parser_version),
                )
                latest_by_key[book.book_key] = book.model_dump()
            except (json.JSONDecodeError, ValueError, ValidationError) as exc:
                rejects.append(
                    {
                        "source": "books.toscrape.com",
                        "line_num": line_num,
                        "reason": str(exc),
                        "raw": line[:500],
                    }
                )

    df = pd.DataFrame(list(latest_by_key.values()))
    if not df.empty:
        df.to_parquet(clean_dir / "books_clean.parquet", index=False)
    return df, rejects


def clean_openlibrary(
    raw_path: Path, clean_dir: Path, parser_version: str
) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    clean_dir.mkdir(parents=True, exist_ok=True)
    rejects: List[Dict[str, Any]] = []
    latest_by_title: Dict[str, Dict[str, Any]] = {}

    if not raw_path.exists():
        return pd.DataFrame(), rejects

    now_iso = utc_now_iso()
    with raw_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw_obj = json.loads(line)
                title_norm = raw_obj.get("title_norm", "").strip()
                payload = raw_obj.get("payload")

                if not payload:
                    continue

                ol_title = payload.get("title")
                ol_title_norm = normalize_title(ol_title) if ol_title else ""
                match_score = round(
                    SequenceMatcher(None, title_norm, ol_title_norm).ratio(), 4
                )

                authors = payload.get("author_name") or []
                isbns = payload.get("isbn") or []
                langs = payload.get("language") or []

                rec = OpenLibraryRecord(
                    title_norm=title_norm,
                    ol_work_key=payload.get("key"),
                    ol_title=ol_title,
                    author_name=authors[0] if isinstance(authors, list) and authors else None,
                    first_publish_year=payload.get("first_publish_year"),
                    edition_count=payload.get("edition_count"),
                    isbn=isbns[0] if isinstance(isbns, list) and isbns else None,
                    language=langs[0] if isinstance(langs, list) and langs else None,
                    match_score=match_score,
                    source=raw_obj.get("source", "openlibrary.org"),
                    collected_at=raw_obj.get("collected_at", now_iso),
                    updated_at=now_iso,
                    parser_version=raw_obj.get("parser_version", parser_version),
                )
                # Оставляем только уверенные совпадения (>= 0.6)
                if rec.match_score >= 0.6:
                    latest_by_title[rec.title_norm] = rec.model_dump()
            except (json.JSONDecodeError, ValueError, ValidationError) as exc:
                rejects.append(
                    {
                        "source": "openlibrary.org",
                        "line_num": line_num,
                        "reason": str(exc),
                        "raw": line[:500],
                    }
                )

    df = pd.DataFrame(list(latest_by_title.values()))
    if not df.empty:
        df.to_parquet(clean_dir / "openlibrary_clean.parquet", index=False)
    return df, rejects


def save_rejects(rejects: List[Dict[str, Any]], clean_dir: Path) -> None:
    rejects_path = clean_dir / "rejects.jsonl"
    with rejects_path.open("w", encoding="utf-8") as f:
        for item in rejects:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
