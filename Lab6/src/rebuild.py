"""Полная пересборка слоёв clean/, db/ и mart/ из неизменяемого слоя raw/ без обращения к сети."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.load import run_constraint_tests, run_idempotency_demo
from src.clean import load_raw_sources, run_cleaning_pipeline
from src.contract import run_contract_probes, validate_clean_tables
from src.quality import enforce_quality_gate, evaluate_quality


def run_format_benchmark(books_df: pd.DataFrame, clean_dir: Path) -> pd.DataFrame:
    """Сравнение CSV, JSON Lines и Parquet по размеру, скорости чтения и сохранности типов."""
    bench = books_df.copy()
    bench["price_str"] = bench["price"].map(lambda x: f"{x:.2f}" if x is not None else None)
    # Код с ведущим нулём для демонстрации потери ведущего нуля в CSV
    bench["catalog_code"] = [f"0{1000 + i}" for i in range(len(bench))]
    bench = pd.concat([bench[["book_key", "title", "category", "price_str", "rating", "collected_at", "catalog_code"]]] * 100, ignore_index=True)

    tmp_csv = clean_dir / "_bench_tmp.csv"
    tmp_jsonl = clean_dir / "_bench_tmp.jsonl"
    tmp_parquet = clean_dir / "_bench_tmp.parquet"

    bench.to_csv(tmp_csv, index=False)
    bench.to_json(tmp_jsonl, orient="records", lines=True, force_ascii=False, date_format="iso")
    bench.to_parquet(tmp_parquet, index=False, compression="snappy")

    results = []
    for fmt_name, fpath in [("CSV", tmp_csv), ("JSON Lines", tmp_jsonl), ("Parquet (snappy)", tmp_parquet)]:
        t0 = time.perf_counter()
        if fmt_name == "CSV":
            df_full = pd.read_csv(fpath)
        elif fmt_name == "JSON Lines":
            df_full = pd.read_json(fpath, lines=True)
        else:
            df_full = pd.read_parquet(fpath)
        t_full_ms = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        if fmt_name == "CSV":
            _ = pd.read_csv(fpath, usecols=["book_key", "price_str"])
        elif fmt_name == "JSON Lines":
            _ = pd.read_json(fpath, lines=True)[["book_key", "price_str"]]
        else:
            _ = pd.read_parquet(fpath, columns=["book_key", "price_str"])
        t_cols_ms = (time.perf_counter() - t0) * 1000.0

        results.append({
            "формат": fmt_name,
            "строк": len(df_full),
            "размер_КБ": round(fpath.stat().st_size / 1024.0, 2),
            "чтение_всего_мс": round(t_full_ms, 2),
            "чтение_2_столбцов_мс": round(t_cols_ms, 2),
            "тип_collected_at": str(df_full["collected_at"].dtype),
            "пример_catalog_code": str(df_full["catalog_code"].iloc[0]),
        })

    for p in (tmp_csv, tmp_jsonl, tmp_parquet):
        p.unlink(missing_ok=True)

    bench_df = pd.DataFrame(results)
    bench_df.to_csv(clean_dir / "format_benchmark.csv", index=False)
    return bench_df


def build_mart(books_df: pd.DataFrame, authors_df: pd.DataFrame, mart_dir: Path) -> dict:
    """Формирует 3 аналитических представления и партиционированный по дате снапшот в mart/."""
    views_dir = mart_dir / "views"
    snaps_dir = mart_dir / "snapshots"
    views_dir.mkdir(parents=True, exist_ok=True)
    snaps_dir.mkdir(parents=True, exist_ok=True)

    work = books_df.copy()
    work["price_num"] = work["price"].map(lambda x: float(x) if x is not None else None)

    # Представление 1: Агрегаты по категориям
    cat_summary = (
        work.fillna({"category": "Без категории"})
        .groupby("category", as_index=False)
        .agg(
            книг=("book_key", "count"),
            средняя_цена=("price_num", "mean"),
            медианная_цена=("price_num", "median"),
            средний_рейтинг=("rating", "mean"),
            в_наличии=("in_stock", lambda s: int(s.fillna(False).sum())),
        )
        .round(2)
        .sort_values("книг", ascending=False)
        .reset_index(drop=True)
    )
    cat_summary.to_parquet(views_dir / "category_summary.parquet", index=False)

    # Представление 2: Статистика по авторам (JOIN books и authors по author_key)
    joined = work.merge(authors_df[["author_key", "author_name", "birth_year", "top_work", "work_count"]], on="author_key", how="left")
    author_stats = (
        joined.groupby(["author_key", "author_name", "birth_year", "work_count"], dropna=False, as_index=False)
        .agg(
            книг_в_каталоге=("book_key", "count"),
            средний_рейтинг=("rating", "mean"),
            средний_год_публикации=("publish_year", "mean"),
        )
        .round(2)
        .sort_values(["книг_в_каталоге", "средний_рейтинг"], ascending=[False, False])
        .reset_index(drop=True)
    )
    author_stats.to_parquet(views_dir / "author_stats.parquet", index=False)

    # Представление 3: Распределение по ценовым диапазонам и источникам
    priced = work.dropna(subset=["price_num"]).copy()
    priced["price_band"] = pd.cut(
        priced["price_num"],
        bins=[0, 20, 35, 50, 10000],
        labels=["<20", "20-35", "35-50", "50+"],
    )
    price_bands = (
        priced.groupby("price_band", observed=True, as_index=False)
        .agg(
            книг=("book_key", "count"),
            средняя_цена=("price_num", "mean"),
            средний_рейтинг=("rating", "mean"),
            суммарный_остаток=("stock", "sum"),
        )
        .round(2)
    )
    price_bands.to_parquet(views_dir / "price_rating_bands.parquet", index=False)

    # Партиционированный снапшот по дате сбора dt=YYYY-MM-DD
    snap = work.drop(columns=["price_num"]).copy()
    snap["price"] = snap["price"].map(lambda x: f"{x:.2f}" if x is not None else None)
    snap["dt"] = pd.to_datetime(snap["collected_at"], utc=True).dt.strftime("%Y-%m-%d")
    for dt_val, part_df in snap.groupby("dt"):
        part_dir = snaps_dir / f"dt={dt_val}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part_df.drop(columns=["dt"]).to_parquet(part_dir / "data.parquet", index=False)

    partitions = sorted(p.name for p in snaps_dir.iterdir() if p.is_dir())
    sample_part = pd.read_parquet(snaps_dir / partitions[0] / "data.parquet")

    return {
        "cat_summary": cat_summary,
        "author_stats": author_stats,
        "price_bands": price_bands,
        "partitions": partitions,
        "sample_partition_rows": len(sample_part),
    }


def main() -> None:
    raw_dir = ROOT / "raw"
    clean_dir = ROOT / "clean"
    db_dir = ROOT / "db"
    mart_dir = ROOT / "mart"

    print("=== 1. Чтение сырого слоя (raw/) ===")
    raw_df = load_raw_sources(raw_dir)
    print(f"Всего сырых записей: {len(raw_df)} (файлы: {raw_df['_raw_file'].unique().tolist()})")

    print("\n=== 2. Шестишаговая очистка ===")
    books_df, authors_df, rejects_df, protocol_df = run_cleaning_pipeline(raw_df)
    print(protocol_df.to_string(index=False))

    print("\n=== 3. Валидация контрактом Pydantic ===")
    valid_authors, valid_books, contract_rejects = validate_clean_tables(books_df, authors_df)
    if contract_rejects:
        rejects_df = pd.concat([rejects_df, pd.DataFrame(contract_rejects)], ignore_index=True)
    print(f"Валидных авторов: {len(valid_authors)} | Валидных книг: {len(valid_books)} | Отбраковано: {len(rejects_df)}")

    probes_df = run_contract_probes(valid_books[0])
    print("\nПроверка контракта на 6 битых записях:")
    print(probes_df.to_string(index=False))

    # Сохранение чистого слоя в Parquet и rejects.jsonl
    clean_books_out = pd.DataFrame([
        {**b.model_dump(), "price": f"{b.price:.2f}" if b.price is not None else None}
        for b in valid_books
    ])
    clean_books_out["collected_at"] = pd.to_datetime(clean_books_out["collected_at"], utc=True)
    clean_books_out["stock"] = clean_books_out["stock"].astype("Int64")
    clean_books_out["rating"] = clean_books_out["rating"].astype("Int64")
    clean_books_out["publish_year"] = clean_books_out["publish_year"].astype("Int64")

    clean_authors_out = pd.DataFrame([a.model_dump() for a in valid_authors])
    clean_authors_out["collected_at"] = pd.to_datetime(clean_authors_out["collected_at"], utc=True)
    clean_authors_out["birth_year"] = clean_authors_out["birth_year"].astype("Int64")
    clean_authors_out["work_count"] = clean_authors_out["work_count"].astype("Int64")

    clean_books_out.to_parquet(clean_dir / "books.parquet", index=False)
    clean_authors_out.to_parquet(clean_dir / "authors.parquet", index=False)
    rejects_df.to_json(clean_dir / "rejects.jsonl", orient="records", lines=True, force_ascii=False)

    print("\n=== 4. Сравнение форматов (CSV / JSONL / Parquet) ===")
    bench_df = run_format_benchmark(books_df, clean_dir)
    print(bench_df.to_string(index=False))

    print("\n=== 5. Идемпотентная загрузка в SQLite (3 прогона) и проверка ограничений ===")
    db_path = db_dir / "warehouse.db"
    schema_path = db_dir / "schema.sql"
    runs_df, history_df = run_idempotency_demo(db_path, schema_path, valid_authors, valid_books, rejects_df)
    print(runs_df.to_string(index=False))
    print("\nИстория изменений (book_history):")
    print(history_df.to_string(index=False))

    constraints_df = run_constraint_tests(db_path)
    print("\nПроверка ограничений БД:")
    print(constraints_df.to_string(index=False))

    print("\n=== 6. Метрики качества и светофор ===")
    stats_file = mart_dir / "last_run_stats.json"
    ref_time = pd.to_datetime(books_df["collected_at"], utc=True).max().to_pydatetime()
    quality_df, verdict = evaluate_quality(raw_df, books_df, rejects_df, prev_stats_path=stats_file, reference_now=ref_time)
    print(quality_df[["метрика", "значение", "пороги (зелёный / жёлтый)", "статус"]].to_string(index=False))
    print(f"Итоговый вердикт: {verdict}")

    enforce_quality_gate(quality_df)
    stats_file.write_text(json.dumps({"clean_books_count": len(valid_books)}, indent=2), encoding="utf-8")

    print("\n=== 7. Формирование витрин и партиций mart/ ===")
    mart_info = build_mart(books_df, authors_df, mart_dir)
    print(f"Партиции снапшотов: {mart_info['partitions']} (записей в первой партиции: {mart_info['sample_partition_rows']})")


if __name__ == "__main__":
    main()
