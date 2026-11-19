"""Smoke-check качества данных и деградации между прогонами (п. 4.6 методички).

Проверяет:
1. Падение числа собранных записей между текущим и предыдущим прогоном > 20%.
2. Долю пустых обязательных полей в БД > 5%.

Возвращает код 0 при успешной проверке и код 1 при обнаружении деградации.
"""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

REQUIRED_DB_FIELDS = (
    "upc",
    "title",
    "price",
    "price_excl_tax",
    "stock",
    "reviews",
    "category",
    "category_from_menu",
    "description",
    "url",
)


def load_stats(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Файл статистики не найден: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def check_db_completeness(db_path: Path, max_empty_pct: float = 5.0) -> tuple[int, dict[str, float], list[str]]:
    if not db_path.exists():
        raise FileNotFoundError(f"База данных не найдена: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        if total == 0:
            return 0, {}, ["CRITICAL: В таблице books 0 записей."]

        empty_rates: dict[str, float] = {}
        warnings: list[str] = []
        for col in REQUIRED_DB_FIELDS:
            empty_cnt = conn.execute(
                f"SELECT COUNT(*) FROM books WHERE {col} IS NULL OR TRIM(CAST({col} AS TEXT)) = ''"
            ).fetchone()[0]
            empty_pct = round((empty_cnt / total) * 100.0, 2)
            empty_rates[col] = empty_pct
            if empty_pct > max_empty_pct:
                warnings.append(
                    f"WARNING: Поле '{col}' пусто в {empty_pct}% записей ({empty_cnt}/{total}), порог > {max_empty_pct}%."
                )
        return total, empty_rates, warnings
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check деградации прогонов Scrapy.")
    parser.add_argument("--current", type=Path, required=True, help="Путь к stats_<current>.json")
    parser.add_argument("--previous", type=Path, required=False, help="Путь к stats_<previous>.json")
    parser.add_argument("--db", type=Path, default=Path("data/crawl.db"), help="Путь к SQLite БД")
    parser.add_argument("--max-drop-pct", type=float, default=20.0, help="Допустимое падение записей, %%")
    parser.add_argument("--max-empty-pct", type=float, default=5.0, help="Допустимая доля пустых полей, %%")
    args = parser.parse_args()

    warnings: list[str] = []

    curr_stats = load_stats(args.current)
    curr_count = int(curr_stats.get("item_scraped_count", 0))
    print(f"Текущий прогон ({args.current.name}): item_scraped_count = {curr_count}")

    if args.previous:
        prev_stats = load_stats(args.previous)
        prev_count = int(prev_stats.get("item_scraped_count", 0))
        print(f"Предыдущий прогон ({args.previous.name}): item_scraped_count = {prev_count}")
        if prev_count > 0:
            drop_pct = round(((prev_count - curr_count) / prev_count) * 100.0, 2)
            print(f"Изменение объёма: {-drop_pct:+.2f}% (падение: {drop_pct:.2f}%)")
            if drop_pct > args.max_drop_pct:
                warnings.append(
                    f"WARNING: Число записей упало на {drop_pct:.2f}% (с {prev_count} до {curr_count}), "
                    f"что превышает порог {args.max_drop_pct}%."
                )
        else:
            warnings.append("WARNING: В предыдущем прогоне 0 записей — сравнение объёма некорректно.")

    total_db, empty_rates, db_warnings = check_db_completeness(args.db, max_empty_pct=args.max_empty_pct)
    warnings.extend(db_warnings)

    print(f"\nПроверка БД ({args.db}): всего строк = {total_db}")
    for col, pct in empty_rates.items():
        print(f"  {col:<20s} пустых: {pct:5.2f}% (полнота: {100.0 - pct:6.2f}%)")

    if warnings:
        print("\n=== ОБНАРУЖЕНЫ ПРЕДУПРЕЖДЕНИЯ ===")
        for w in warnings:
            print(" -", w)
        return 1

    print("\nOK: Деградация не обнаружена, все пороги качества соблюдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
