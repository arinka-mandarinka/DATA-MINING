"""Идемпотентная пакетная загрузка в SQLite с UPSERT, историей изменений и проверкой ограничений."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from src.contract import AuthorContract, BookContract

UPSERT_AUTHORS_SQL = """
INSERT INTO authors (
    author_key, author_name, birth_year, top_work, work_count,
    source, source_url, parser_version, collected_at, updated_at
)
VALUES (
    :author_key, :author_name, :birth_year, :top_work, :work_count,
    :source, :source_url, :parser_version, :collected_at, :updated_at
)
ON CONFLICT(author_key) DO UPDATE SET
    author_name    = excluded.author_name,
    birth_year     = excluded.birth_year,
    top_work       = excluded.top_work,
    work_count     = excluded.work_count,
    collected_at   = excluded.collected_at,
    updated_at     = excluded.updated_at
WHERE authors.author_name IS NOT excluded.author_name
   OR authors.birth_year  IS NOT excluded.birth_year
   OR authors.top_work    IS NOT excluded.top_work
   OR authors.work_count  IS NOT excluded.work_count;
"""

UPSERT_BOOKS_SQL = """
INSERT INTO books (
    book_key, author_key, title, category, price, stock, rating, in_stock,
    publish_year, isbn, source, source_url, parser_version, collected_at, updated_at
)
VALUES (
    :book_key, :author_key, :title, :category, :price, :stock, :rating, :in_stock,
    :publish_year, :isbn, :source, :source_url, :parser_version, :collected_at, :updated_at
)
ON CONFLICT(book_key) DO UPDATE SET
    author_key     = excluded.author_key,
    title          = excluded.title,
    category       = excluded.category,
    price          = excluded.price,
    stock          = excluded.stock,
    rating         = excluded.rating,
    in_stock       = excluded.in_stock,
    publish_year   = excluded.publish_year,
    isbn           = excluded.isbn,
    collected_at   = excluded.collected_at,
    updated_at     = excluded.updated_at
WHERE books.title        IS NOT excluded.title
   OR books.category     IS NOT excluded.category
   OR books.price        IS NOT excluded.price
   OR books.stock        IS NOT excluded.stock
   OR books.rating       IS NOT excluded.rating
   OR books.in_stock     IS NOT excluded.in_stock
   OR books.publish_year IS NOT excluded.publish_year;
"""

INSERT_HISTORY_SQL = """
INSERT OR REPLACE INTO book_history (book_key, observed_at, price, stock, rating)
VALUES (:book_key, :observed_at, :price, :stock, :rating);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path, schema_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.commit()


def load_batch(
    conn: sqlite3.Connection,
    authors: list[AuthorContract],
    books: list[BookContract],
    run_ts: str,
) -> dict:
    """Выполняет пакетный UPSERT авторов и книг и записывает снапшот в book_history."""
    conn.execute("PRAGMA foreign_keys = ON;")

    author_rows = [
        {
            "author_key": a.author_key,
            "author_name": a.author_name,
            "birth_year": a.birth_year,
            "top_work": a.top_work,
            "work_count": a.work_count,
            "source": a.source,
            "source_url": a.source_url,
            "parser_version": a.parser_version,
            "collected_at": a.collected_at.isoformat(),
            "updated_at": run_ts,
        }
        for a in authors
    ]
    conn.executemany(UPSERT_AUTHORS_SQL, author_rows)

    book_rows = [
        {
            "book_key": b.book_key,
            "author_key": b.author_key,
            "title": b.title,
            "category": b.category,
            "price": f"{b.price:.2f}" if b.price is not None else None,
            "stock": b.stock,
            "rating": b.rating,
            "in_stock": None if b.in_stock is None else int(b.in_stock),
            "publish_year": b.publish_year,
            "isbn": b.isbn,
            "source": b.source,
            "source_url": b.source_url,
            "parser_version": b.parser_version,
            "collected_at": b.collected_at.isoformat(),
            "updated_at": run_ts,
        }
        for b in books
    ]
    conn.executemany(UPSERT_BOOKS_SQL, book_rows)

    history_rows = [
        {
            "book_key": r["book_key"],
            "observed_at": run_ts,
            "price": r["price"],
            "stock": r["stock"],
            "rating": r["rating"],
        }
        for r in book_rows
    ]
    conn.executemany(INSERT_HISTORY_SQL, history_rows)
    conn.commit()

    total_authors = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
    total_books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    updated_books = conn.execute("SELECT COUNT(*) FROM books WHERE updated_at = ?", (run_ts,)).fetchone()[0]
    updated_authors = conn.execute("SELECT COUNT(*) FROM authors WHERE updated_at = ?", (run_ts,)).fetchone()[0]

    return {
        "run_ts": run_ts,
        "передано_книг": len(book_rows),
        "всего_книг_в_БД": total_books,
        "обновлено_книг": updated_books,
        "всего_авторов_в_БД": total_authors,
        "обновлено_авторов": updated_authors,
    }


def load_rejects(conn: sqlite3.Connection, rejects_df: pd.DataFrame, run_ts: str) -> int:
    conn.execute("DELETE FROM rejects;")
    if rejects_df.empty:
        conn.commit()
        return 0
    rows = []
    for _, r in rejects_df.iterrows():
        payload_dict = {
            k: (None if pd.isna(v) else str(v))
            for k, v in r.items()
            if k not in {"field_name", "reason"}
        }
        rows.append({
            "book_key": None if pd.isna(r.get("book_key")) else str(r.get("book_key")),
            "source": None if pd.isna(r.get("source")) else str(r.get("source")),
            "field_name": None if pd.isna(r.get("field_name")) else str(r.get("field_name")),
            "reason": str(r.get("reason", "unknown")),
            "payload": json.dumps(payload_dict, ensure_ascii=False),
            "seen_at": run_ts,
        })
    conn.executemany(
        """
        INSERT INTO rejects (book_key, source, field_name, reason, payload, seen_at)
        VALUES (:book_key, :source, :field_name, :reason, :payload, :seen_at);
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def run_idempotency_demo(
    db_path: Path,
    schema_path: Path,
    authors: list[AuthorContract],
    books: list[BookContract],
    rejects_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Три прогона загрузки: 1) исходный, 2) повторный без изменений, 3) изменение 2 записей."""
    if db_path.exists():
        db_path.unlink()
    init_db(db_path, schema_path)

    with get_connection(db_path) as conn:
        t1 = "2026-11-19T10:00:00+00:00"
        res1 = load_batch(conn, authors, books, t1)
        res1["сценарий"] = "Прогон 1: первичная загрузка"

        t2 = "2026-11-19T11:00:00+00:00"
        res2 = load_batch(conn, authors, books, t2)
        res2["сценарий"] = "Прогон 2: повтор с теми же данными"

        # Прогон 3: меняем цену у 1-й книги и остаток/рейтинг у 2-й книги
        modified_books = [b.model_copy() for b in books]
        b0 = modified_books[0]
        old_price = b0.price if b0.price is not None else Decimal("25.00")
        modified_books[0] = b0.model_copy(update={"price": (old_price + Decimal("5.00")).quantize(Decimal("0.01"))})

        b1 = modified_books[1]
        new_stock = (b1.stock + 4) if b1.stock is not None else 7
        modified_books[1] = b1.model_copy(update={"stock": new_stock, "in_stock": True})

        t3 = "2026-11-19T12:00:00+00:00"
        res3 = load_batch(conn, authors, modified_books, t3)
        res3["сценарий"] = "Прогон 3: изменены 2 записи"

        load_rejects(conn, rejects_df, t3)

        changed_keys = (modified_books[0].book_key, modified_books[1].book_key)
        history_df = pd.read_sql_query(
            """
            SELECT h.book_key, b.title, h.observed_at, h.price, h.stock, h.rating
            FROM book_history h
            JOIN books b USING (book_key)
            WHERE h.book_key IN (?, ?)
            ORDER BY h.book_key, h.observed_at
            """,
            conn,
            params=changed_keys,
        )

    runs_df = pd.DataFrame([res1, res2, res3])[
        ["сценарий", "передано_книг", "всего_книг_в_БД", "обновлено_книг", "всего_авторов_в_БД", "обновлено_авторов"]
    ]
    return runs_df, history_df


def run_constraint_tests(db_path: Path) -> pd.DataFrame:
    """Демонстрация работы ограничений БД (CHECK, PRIMARY KEY, FOREIGN KEY)."""
    with get_connection(db_path) as conn:
        sample = conn.execute("SELECT book_key, author_key FROM books LIMIT 1").fetchone()
        existing_book_key, existing_author_key = sample[0], sample[1]

        cases = [
            (
                "1. Отрицательная цена (CHECK price > 0)",
                """
                INSERT INTO books VALUES (
                    'upc:test_neg_price', ?, 'Тестовая книга', 'Fiction', '-12.50', 5, 4, 1,
                    2020, NULL, 'test', 'https://example.com/1', 'v1', '2026-11-19T12:00:00+00:00', '2026-11-19T12:00:00+00:00'
                )
                """,
                (existing_author_key,),
            ),
            (
                "2. Рейтинг вне диапазона 1..5 (CHECK rating BETWEEN 1 AND 5)",
                """
                INSERT INTO books VALUES (
                    'upc:test_bad_rating', ?, 'Тестовая книга', 'Fiction', '15.00', 5, 9, 1,
                    2020, NULL, 'test', 'https://example.com/2', 'v1', '2026-11-19T12:00:00+00:00', '2026-11-19T12:00:00+00:00'
                )
                """,
                (existing_author_key,),
            ),
            (
                "3. Дублирующийся первичный ключ (PRIMARY KEY book_key)",
                """
                INSERT INTO books VALUES (
                    ?, ?, 'Дубль книги', 'Fiction', '20.00', 3, 4, 1,
                    2020, NULL, 'test', 'https://example.com/3', 'v1', '2026-11-19T12:00:00+00:00', '2026-11-19T12:00:00+00:00'
                )
                """,
                (existing_book_key, existing_author_key),
            ),
            (
                "4. Несуществующий автор (FOREIGN KEY author_key)",
                """
                INSERT INTO books VALUES (
                    'upc:test_bad_fk', 'ol_author:NON_EXISTENT_999', 'Книга без автора', 'Fiction', '20.00', 3, 4, 1,
                    2020, NULL, 'test', 'https://example.com/4', 'v1', '2026-11-19T12:00:00+00:00', '2026-11-19T12:00:00+00:00'
                )
                """,
                (),
            ),
        ]

        results = []
        for title, sql, params in cases:
            try:
                conn.execute(sql, params)
                conn.commit()
                results.append({"проверка ограничения БД": title, "результат": "ВСТАВЛЕНО (ошибка)", "сообщение СУБД": ""})
            except sqlite3.IntegrityError as exc:
                results.append({
                    "проверка ограничения БД": title,
                    "результат": "отклонено СУБД",
                    "сообщение СУБД": str(exc),
                })

    return pd.DataFrame(results)
