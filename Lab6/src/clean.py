"""Шестишаговый пайплайн очистки разнородных источников (HTML + API) с протоколированием.

Строгая последовательность (раздел 5.3 задания):
1. Нормализация текста (NFKC, удаление \xa0 и zero-width, схлопывание пробелов, обрезка).
2. Формирование стабильного бизнес-ключа (book_key, author_key) и очистка URL от параметров трекинга.
3. Типизация (Decimal для цен без float, nullable Int64 для целых с пропусками, UTC для дат).
4. Обработка пропусков (обязательные -> rejects с причиной; необязательные -> честный NULL).
5. Дедупликация (полные дубли, дубли по бизнес-ключу и «почти-дубли»; сохраняется самая свежая запись).
6. Бизнес-правила предметной области (>= 5 правил; нарушения -> rejects с причиной).
"""

from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

RATING_MAP = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
CURRENT_YEAR = 2026


def normalize_text(val: Any) -> str | None:
    """Шаг 1: NFKC, удаление неразрывных и zero-width пробелов, схлопывание пробелов."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\xa0", " ")
    s = ZERO_WIDTH_RE.sub("", s)
    s = " ".join(s.split()).strip()
    return s if s else None


def normalize_url(val: Any) -> str | None:
    s = normalize_text(val)
    if not s:
        return None
    return s.split("?")[0].rstrip("/")


def parse_decimal_price(val: Any) -> Decimal | None:
    s = normalize_text(val)
    if not s:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", s)
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_int_nullable(val: Any) -> int | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = normalize_text(val)
    if not s:
        return None
    m = re.search(r"-?\d+", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def load_raw_sources(raw_dir: Path) -> pd.DataFrame:
    """Чтение всех .jsonl файлов из слоя raw/ без их модификации."""
    frames = []
    for path in sorted(raw_dir.glob("*.jsonl")):
        records = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        if records:
            df_part = pd.DataFrame(records)
            df_part["_raw_file"] = path.name
            frames.append(df_part)
    if not frames:
        raise FileNotFoundError(f"В каталоге {raw_dir} не найдены непустые .jsonl файлы")
    return pd.concat(frames, ignore_index=True)


def run_cleaning_pipeline(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Выполняет 6 шагов очистки и возвращает (books_df, authors_df, rejects_df, protocol_df)."""
    df = raw_df.copy()
    protocol: list[dict] = []
    rejects_list: list[dict] = []

    def log_step(step_name: str, before: int, after: int, changed: int, note: str) -> None:
        protocol.append({
            "шаг": step_name,
            "строк до": before,
            "строк после": after,
            "изменено/удалено": changed,
            "примечание": note,
        })

    def push_rejects(bad_df: pd.DataFrame, field_name: str, reason: str) -> None:
        for _, r in bad_df.iterrows():
            rejects_list.append({
                "book_key": r.get("book_key"),
                "source": r.get("_source"),
                "field_name": field_name,
                "reason": reason,
                "title_raw": r.get("title_raw"),
                "price_raw": r.get("price_raw"),
                "detail_url_raw": r.get("detail_url_raw"),
                "_collected_at": r.get("_collected_at"),
                "_is_synthetic_dirty": bool(r.get("_is_synthetic_dirty", False)),
            })

    # =========================================================================
    # ШАГ 1. Нормализация текста
    # =========================================================================
    n0 = len(df)
    text_cols = [
        "title_raw", "category_raw", "author_name_raw",
        "author_top_work_raw", "upc_raw", "isbn_raw", "work_key", "author_key_raw"
    ]
    changed_cells = 0
    for col in text_cols:
        if col not in df.columns:
            df[col] = None
        norm_series = df[col].map(normalize_text)
        orig_str = df[col].fillna("").astype(str)
        new_str = norm_series.fillna("").astype(str)
        changed_cells += int((orig_str != new_str).sum())
        df[col] = norm_series

    df["title"] = df["title_raw"]
    df["category"] = df["category_raw"]
    df["author_name"] = df["author_name_raw"]
    df["author_top_work"] = df["author_top_work_raw"]
    log_step(
        "1. Нормализация текста",
        n0, len(df), changed_cells,
        f"NFKC, удаление \\xa0 и zero-width, нормализовано ячеек: {changed_cells}"
    )

    # =========================================================================
    # ШАГ 2. Формирование бизнес-ключа и нормализация URL
    # =========================================================================
    n_before = len(df)
    df["source"] = df["_source"].map(normalize_text)
    df["source_url"] = df["detail_url_raw"].map(normalize_url)
    df["parser_version"] = df["_parser_version"].map(normalize_text)

    def make_book_key(row: pd.Series) -> str | None:
        src = row.get("source")
        if src == "books_toscrape_html":
            upc = normalize_text(row.get("upc_raw"))
            if upc:
                return f"upc:{upc.lower()}"
            return None
        if src == "openlibrary_api":
            wkey = normalize_text(row.get("work_key"))
            if wkey:
                return f"olw:{wkey}"
            isbn = normalize_text(row.get("isbn_raw"))
            if isbn:
                return f"isbn:{isbn}"
            return None
        return None

    def make_author_key(row: pd.Series) -> str | None:
        src = row.get("source")
        if src == "books_toscrape_html":
            # HTML-каталог books.toscrape.com не содержит автора -> связываем со справочной записью
            return "unknown_toscrape"
        if src == "openlibrary_api":
            akey = normalize_text(row.get("author_key_raw"))
            if akey:
                return f"ol_author:{akey}"
            return None
        return None

    df["book_key"] = df.apply(make_book_key, axis=1)
    df["author_key"] = df.apply(make_author_key, axis=1)
    df.loc[df["source"] == "books_toscrape_html", "author_name"] = "Не указан (каталог Books to Scrape)"
    df.loc[df["source"] == "books_toscrape_html", "author_top_work"] = None

    url_trimmed = int((df["detail_url_raw"].fillna("").astype(str) != df["source_url"].fillna("").astype(str)).sum())
    log_step(
        "2. Бизнес-ключ и URL",
        n_before, len(df), url_trimmed,
        f"Ключи upc:/olw: и ol_author:; очищено URL с параметрами трекинга: {url_trimmed}"
    )

    # =========================================================================
    # ШАГ 3. Типизация (Decimal для денег, nullable Int64, UTC datetime)
    # =========================================================================
    n_before = len(df)
    df["price"] = df["price_raw"].map(parse_decimal_price)

    def parse_rating(row: pd.Series) -> int | None:
        if row.get("source") == "books_toscrape_html":
            r_raw = normalize_text(row.get("rating_raw"))
            if not r_raw:
                return None
            word = r_raw.split()[-1]
            if word in RATING_MAP:
                return RATING_MAP[word]
            # Если пришло числовое значение вне словаря (например, "Seven" или "7")
            if word == "Seven":
                return 7
            return parse_int_nullable(word)
        r_api = row.get("ratings_average_raw")
        if r_api is None or (isinstance(r_api, float) and pd.isna(r_api)):
            return None
        try:
            return int(round(float(r_api)))
        except (ValueError, TypeError):
            return None

    def parse_in_stock(row: pd.Series) -> bool | None:
        avail = normalize_text(row.get("availability_raw"))
        if not avail:
            return None
        if "in stock" in avail.lower():
            return True
        if "out of stock" in avail.lower():
            return False
        return None

    df["rating"] = pd.Series([parse_rating(r) for _, r in df.iterrows()], index=df.index, dtype="Int64")
    df["stock"] = df["availability_raw"].map(parse_int_nullable).astype("Int64")
    df["in_stock"] = [parse_in_stock(r) for _, r in df.iterrows()]
    df["publish_year"] = df["first_publish_year_raw"].map(parse_int_nullable).astype("Int64")
    df["birth_year"] = df["author_birth_date_raw"].map(parse_int_nullable).astype("Int64")
    df["work_count"] = df["author_work_count_raw"].map(parse_int_nullable).astype("Int64")
    df["isbn"] = df["isbn_raw"].map(normalize_text)
    df["collected_at"] = pd.to_datetime(df["_collected_at"], utc=True, format="mixed", errors="coerce")

    unparsed_html_price = int(((df["source"] == "books_toscrape_html") & df["price_raw"].notna() & df["price"].isna()).sum())
    log_step(
        "3. Типизация",
        n_before, len(df), unparsed_html_price,
        f"Decimal для цен (не разобрано: {unparsed_html_price}), Int64 для целых, UTC для дат"
    )

    # =========================================================================
    # ШАГ 4. Обработка пропусков (обязательные -> rejects, необязательные -> NULL)
    # =========================================================================
    n_before = len(df)
    mandatory_cols = ["book_key", "title", "author_key", "source", "source_url", "collected_at"]
    missing_mandatory_mask = df[mandatory_cols].isna().any(axis=1)
    # Дополнительно проверяем корректность схемы URL
    bad_url_mask = ~df["source_url"].fillna("").str.startswith(("http://", "https://"))
    # Для магазина books_toscrape_html цена является обязательным атрибутом карточки товара
    missing_store_price_mask = (df["source"] == "books_toscrape_html") & df["price"].isna()

    step4_bad_mask = missing_mandatory_mask | bad_url_mask | missing_store_price_mask
    if step4_bad_mask.any():
        for idx, r in df[step4_bad_mask].iterrows():
            if pd.isna(r["book_key"]):
                fld, rsn = "book_key", "пропуск обязательного бизнес-ключа (UPC / Work ID)"
            elif pd.isna(r["title"]):
                fld, rsn = "title", "пропуск обязательного названия книги"
            elif pd.isna(r["author_key"]):
                fld, rsn = "author_key", "пропуск обязательного ключа автора"
            elif pd.isna(r["collected_at"]):
                fld, rsn = "collected_at", "некорректная или отсутствующая метка времени UTC"
            elif not str(r["source_url"] or "").startswith(("http://", "https://")):
                fld, rsn = "source_url", "некорректный URL источника (отсутствует схема http/https)"
            else:
                fld, rsn = "price", "отсутствует или не распознана обязательная цена для HTML-каталога"
            push_rejects(df.loc[[idx]], fld, rsn)

    df = df[~step4_bad_mask].copy().reset_index(drop=True)
    removed_step4 = n_before - len(df)
    log_step(
        "4. Обработка пропусков",
        n_before, len(df), removed_step4,
        f"Отбраковано в rejects: {removed_step4}; необязательные поля оставлены как NULL"
    )

    # =========================================================================
    # ШАГ 5. Дедупликация (полные, по ключу, «почти-дубли»; остаётся самая свежая)
    # =========================================================================
    n_before = len(df)
    dup_count = int(df.duplicated(subset=["book_key"]).sum())
    df = (
        df.sort_values("collected_at", ascending=True)
        .drop_duplicates(subset=["book_key"], keep="last")
        .reset_index(drop=True)
    )
    removed_step5 = n_before - len(df)
    log_step(
        "5. Дедупликация",
        n_before, len(df), removed_step5,
        f"Удалено полных и «почти-дублей» по book_key: {dup_count} (сохранена свежая запись)"
    )

    # =========================================================================
    # ШАГ 6. Бизнес-правила предметной области (>= 5 правил)
    # =========================================================================
    n_before = len(df)

    rule_price_bounds = df["price"].map(lambda p: True if p is None else (Decimal("0") < p <= Decimal("10000")))
    rule_rating_range = df["rating"].isna() | df["rating"].between(1, 5)
    rule_stock_non_neg = df["stock"].isna() | (df["stock"] >= 0)
    rule_stock_avail = ~(
        ((df["in_stock"] == True) & df["stock"].notna() & (df["stock"] == 0)) |
        ((df["in_stock"] == False) & df["stock"].notna() & (df["stock"] > 0))
    )
    rule_publish_year = df["publish_year"].isna() | df["publish_year"].between(1450, CURRENT_YEAR)
    rule_birth_vs_pub = (
        df["birth_year"].isna() |
        df["publish_year"].isna() |
        (df["birth_year"] <= df["publish_year"])
    )

    rules = {
        "price_bounds (0 < price <= 10000)": rule_price_bounds,
        "rating_range (1..5)": rule_rating_range,
        "stock_non_negative (stock >= 0)": rule_stock_non_neg,
        "stock_availability_consistency": rule_stock_avail,
        "publish_year_range (1450..2026)": rule_publish_year,
        "author_birth_before_publish": rule_birth_vs_pub,
    }

    all_ok = pd.concat(rules.values(), axis=1).all(axis=1)
    bad_rules_df = df[~all_ok]
    if len(bad_rules_df) > 0:
        for idx, r in bad_rules_df.iterrows():
            failed_names = [name for name, mask in rules.items() if not bool(mask.loc[idx])]
            push_rejects(
                df.loc[[idx]],
                failed_names[0].split()[0],
                f"нарушение бизнес-правила: {', '.join(failed_names)}"
            )

    df = df[all_ok].copy().reset_index(drop=True)
    removed_step6 = n_before - len(df)
    log_step(
        "6. Бизнес-правила (6 правил)",
        n_before, len(df), removed_step6,
        f"Отбраковано по бизнес-правилам: {removed_step6}"
    )

    # Формирование двух нормализованных таблиц чистого слоя: authors и books
    authors_df = (
        df.sort_values("collected_at", ascending=True)
        .groupby("author_key", as_index=False)
        .agg(
            author_name=("author_name", "last"),
            birth_year=("birth_year", "last"),
            top_work=("author_top_work", "last"),
            work_count=("work_count", "last"),
            source=("source", "last"),
            source_url=("_source_url", "last"),
            parser_version=("parser_version", "last"),
            collected_at=("collected_at", "max"),
        )
    )
    authors_df["birth_year"] = authors_df["birth_year"].astype("Int64")
    authors_df["work_count"] = authors_df["work_count"].astype("Int64")

    book_cols = [
        "book_key", "author_key", "title", "category", "price",
        "stock", "rating", "in_stock", "publish_year", "isbn",
        "source", "source_url", "parser_version", "collected_at"
    ]
    books_df = df[book_cols].copy()

    rejects_df = pd.DataFrame(rejects_list)
    protocol_df = pd.DataFrame(protocol)
    return books_df, authors_df, rejects_df, protocol_df
