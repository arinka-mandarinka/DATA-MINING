import sqlite3
from pathlib import Path
from typing import Any, Dict

import pandas as pd


def evaluate_quality(
    db_path: Path,
    books_df: pd.DataFrame,
    ol_df: pd.DataFrame,
    rejects_count: int,
    thresholds: Dict[str, float],
) -> Dict[str, Any]:
    """Рассчитывает >= 6 метрик качества и выносит вердикт GREEN / RED."""
    with sqlite3.connect(db_path) as conn:
        db_books = pd.read_sql_query("SELECT * FROM books", conn)
        db_ol = pd.read_sql_query("SELECT * FROM openlibrary_enrichment", conn)

    total_clean = len(db_books)
    total_seen = total_clean + rejects_count
    reject_rate = round(rejects_count / total_seen, 4) if total_seen > 0 else 0.0
    null_price_rate = (
        round(float(db_books["price_gbp"].isna().mean()), 4) if total_clean > 0 else 1.0
    )
    null_rating_rate = (
        round(float(db_books["rating"].isna().mean()), 4) if total_clean > 0 else 1.0
    )
    duplicate_rate = (
        round(float(db_books["book_key"].duplicated().mean()), 4) if total_clean > 0 else 0.0
    )

    if total_clean > 0 and not db_ol.empty:
        enriched_count = int(db_books["title_norm"].isin(set(db_ol["title_norm"])).sum())
        enrichment_rate = round(enriched_count / total_clean, 4)
    else:
        enriched_count = 0
        enrichment_rate = 0.0

    checks = {
        "min_clean_records": {
            "value": total_clean,
            "threshold": thresholds["min_clean_records"],
            "passed": total_clean >= thresholds["min_clean_records"],
        },
        "max_reject_rate": {
            "value": reject_rate,
            "threshold": thresholds["max_reject_rate"],
            "passed": reject_rate <= thresholds["max_reject_rate"],
        },
        "max_null_price_rate": {
            "value": null_price_rate,
            "threshold": thresholds["max_null_price_rate"],
            "passed": null_price_rate <= thresholds["max_null_price_rate"],
        },
        "max_null_rating_rate": {
            "value": null_rating_rate,
            "threshold": thresholds["max_null_rating_rate"],
            "passed": null_rating_rate <= thresholds["max_null_rating_rate"],
        },
        "max_duplicate_rate": {
            "value": duplicate_rate,
            "threshold": thresholds["max_duplicate_rate"],
            "passed": duplicate_rate <= thresholds["max_duplicate_rate"],
        },
        "min_enrichment_rate": {
            "value": enrichment_rate,
            "threshold": thresholds["min_enrichment_rate"],
            "passed": enrichment_rate >= thresholds["min_enrichment_rate"],
        },
    }

    verdict = "GREEN" if all(c["passed"] for c in checks.values()) else "RED"
    return {
        "verdict": verdict,
        "enriched_books_count": enriched_count,
        "checks": checks,
    }
