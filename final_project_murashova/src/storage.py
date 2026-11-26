import sqlite3
from pathlib import Path
from typing import Any, Dict

import pandas as pd


def init_db(db_path: Path, schema_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = schema_path.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)


def upsert_books(db_path: Path, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    sql = """
    INSERT INTO books (
        book_key, title, title_norm, category, price_gbp, rating,
        in_stock, availability_text, product_url, source,
        collected_at, updated_at, parser_version
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(book_key) DO UPDATE SET
        title=excluded.title,
        title_norm=excluded.title_norm,
        category=excluded.category,
        price_gbp=excluded.price_gbp,
        rating=excluded.rating,
        in_stock=excluded.in_stock,
        availability_text=excluded.availability_text,
        product_url=excluded.product_url,
        updated_at=excluded.updated_at,
        parser_version=excluded.parser_version;
    """
    rows = [
        (
            r["book_key"], r["title"], r["title_norm"], r["category"],
            float(r["price_gbp"]), int(r["rating"]), int(r["in_stock"]),
            r["availability_text"], r["product_url"], r["source"],
            r["collected_at"], r["updated_at"], r["parser_version"],
        )
        for _, r in df.iterrows()
    ]
    with sqlite3.connect(db_path) as conn:
        conn.executemany(sql, rows)
        conn.commit()
    return len(rows)


def upsert_openlibrary(db_path: Path, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    sql = """
    INSERT INTO openlibrary_enrichment (
        title_norm, ol_work_key, ol_title, author_name, first_publish_year,
        edition_count, isbn, language, match_score, source,
        collected_at, updated_at, parser_version
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(title_norm) DO UPDATE SET
        ol_work_key=excluded.ol_work_key,
        ol_title=excluded.ol_title,
        author_name=excluded.author_name,
        first_publish_year=excluded.first_publish_year,
        edition_count=excluded.edition_count,
        isbn=excluded.isbn,
        language=excluded.language,
        match_score=excluded.match_score,
        updated_at=excluded.updated_at,
        parser_version=excluded.parser_version;
    """
    rows = []
    for _, r in df.iterrows():
        fpy = None if pd.isna(r["first_publish_year"]) else int(r["first_publish_year"])
        edc = None if pd.isna(r["edition_count"]) else int(r["edition_count"])
        rows.append(
            (
                r["title_norm"], r["ol_work_key"], r["ol_title"], r["author_name"],
                fpy, edc, r["isbn"], r["language"], float(r["match_score"]),
                r["source"], r["collected_at"], r["updated_at"], r["parser_version"],
            )
        )
    with sqlite3.connect(db_path) as conn:
        conn.executemany(sql, rows)
        conn.commit()
    return len(rows)


def build_mart(db_path: Path, mart_dir: Path) -> Dict[str, int]:
    mart_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            DROP VIEW IF EXISTS mart_books_enriched;
            CREATE VIEW mart_books_enriched AS
            SELECT
                b.book_key,
                b.title,
                b.category,
                b.price_gbp,
                b.rating,
                b.in_stock,
                o.author_name,
                o.first_publish_year,
                o.edition_count,
                o.language,
                o.match_score,
                CASE WHEN o.title_norm IS NOT NULL THEN 1 ELSE 0 END AS is_enriched
            FROM books b
            LEFT JOIN openlibrary_enrichment o ON b.title_norm = o.title_norm;

            DROP VIEW IF EXISTS mart_category_summary;
            CREATE VIEW mart_category_summary AS
            SELECT
                category,
                COUNT(*) AS books_count,
                ROUND(AVG(price_gbp), 2) AS avg_price_gbp,
                ROUND(AVG(rating), 2) AS avg_rating,
                SUM(is_enriched) AS enriched_count,
                ROUND(AVG(first_publish_year), 1) AS avg_first_publish_year,
                ROUND(AVG(edition_count), 1) AS avg_edition_count
            FROM mart_books_enriched
            GROUP BY category
            ORDER BY books_count DESC;
            """
        )
        df_enriched = pd.read_sql_query("SELECT * FROM mart_books_enriched", conn)
        df_cat = pd.read_sql_query("SELECT * FROM mart_category_summary", conn)

    df_enriched.to_parquet(mart_dir / "mart_books_enriched.parquet", index=False)
    df_cat.to_csv(mart_dir / "mart_category_summary.csv", index=False)
    return {"mart_books_enriched": len(df_enriched), "mart_category_summary": len(df_cat)}


def record_pipeline_run(db_path: Path, run_meta: Dict[str, Any]) -> None:
    sql = """
    INSERT OR REPLACE INTO pipeline_runs (
        run_id, started_at, finished_at, status, quality_verdict,
        books_collected, books_clean, books_rejected, api_enriched
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            sql,
            (
                run_meta["run_id"],
                run_meta["started_at"],
                run_meta["finished_at"],
                run_meta["status"],
                run_meta["quality_verdict"],
                run_meta["books_collected"],
                run_meta["books_clean"],
                run_meta["books_rejected"],
                run_meta["api_enriched"],
            ),
        )
        conn.commit()
