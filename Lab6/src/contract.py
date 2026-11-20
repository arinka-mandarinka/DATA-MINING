"""Контракт данных на базе Pydantic v2 (граница raw/clean -> db/mart).

Включает:
- Строгие типы (Decimal для цен, timezone-aware UTC datetime, nullable int/str).
- Ограничения диапазонов (gt, ge, le, min_length, max_length, decimal_places).
- 4 собственных field_validator.
- 2 model_validator проверки согласованности полей.
- Демонстрацию на 6 заведомо битых записях.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class AuthorContract(BaseModel):
    author_key: str = Field(min_length=4, max_length=120)
    author_name: str = Field(min_length=1, max_length=250)
    birth_year: int | None = Field(default=None, ge=1000, le=2026)
    top_work: str | None = Field(default=None, max_length=400)
    work_count: int | None = Field(default=None, ge=0, le=100_000)
    source: str = Field(min_length=3, max_length=64)
    source_url: str = Field(min_length=10, max_length=500)
    parser_version: str = Field(min_length=3, max_length=64)
    collected_at: datetime

    @field_validator("author_name")
    @classmethod
    def clean_author_name(cls, v: str) -> str:
        cleaned = " ".join(unicodedata.normalize("NFKC", v).replace("\xa0", " ").split()).strip()
        if not cleaned:
            raise ValueError("Имя автора не может быть пустым")
        return cleaned

    @field_validator("collected_at")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("Дата collected_at должна содержать таймзону UTC (наивные даты запрещены)")
        return v.astimezone(timezone.utc)


class BookContract(BaseModel):
    book_key: str = Field(min_length=4, max_length=120)
    author_key: str = Field(min_length=4, max_length=120)
    title: str = Field(min_length=1, max_length=400)
    category: str | None = Field(default=None, max_length=120)
    price: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("10000"), decimal_places=2)
    stock: int | None = Field(default=None, ge=0, le=100_000)
    rating: int | None = Field(default=None, ge=1, le=5)
    in_stock: bool | None = None
    publish_year: int | None = Field(default=None, ge=1450, le=2026)
    isbn: str | None = Field(default=None, min_length=10, max_length=20)
    source: str = Field(min_length=3, max_length=64)
    source_url: str = Field(min_length=10, max_length=500)
    parser_version: str = Field(min_length=3, max_length=64)
    collected_at: datetime

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        cleaned = " ".join(unicodedata.normalize("NFKC", v).replace("\xa0", " ").split()).strip()
        if not cleaned:
            raise ValueError("Название книги не может состоять только из пробелов")
        return cleaned

    @field_validator("book_key")
    @classmethod
    def validate_book_key_prefix(cls, v: str) -> str:
        if not v.startswith(("upc:", "isbn:", "olw:")):
            raise ValueError("Бизнес-ключ книги должен начинаться с upc:, isbn: или olw:")
        return v

    @field_validator("source_url")
    @classmethod
    def validate_http_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL должен начинаться с http:// или https://")
        return v.split("?")[0]

    @field_validator("collected_at")
    @classmethod
    def validate_utc_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("Метка времени collected_at обязана иметь таймзону UTC")
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_stock_and_availability(self) -> "BookContract":
        if self.in_stock is True and self.stock is not None and self.stock == 0:
            raise ValueError("Несогласованность: книга отмечена в наличии (in_stock=True), но остаток stock=0")
        if self.in_stock is False and self.stock is not None and self.stock > 0:
            raise ValueError("Несогласованность: книга отмечена как отсутствующая, но stock > 0")
        return self

    @model_validator(mode="after")
    def validate_source_requirements(self) -> "BookContract":
        if self.source == "books_toscrape_html" and self.price is None:
            raise ValueError("Для коммерческого HTML-каталога books_toscrape_html поле price обязательно")
        return self


def validate_clean_tables(
    books_df: pd.DataFrame, authors_df: pd.DataFrame
) -> tuple[list[AuthorContract], list[BookContract], list[dict]]:
    """Валидирует очищенные таблицы через Pydantic-контракты."""
    valid_authors: list[AuthorContract] = []
    valid_books: list[BookContract] = []
    contract_rejects: list[dict] = []

    for _, row in authors_df.iterrows():
        payload = {
            "author_key": row["author_key"],
            "author_name": row["author_name"],
            "birth_year": None if pd.isna(row["birth_year"]) else int(row["birth_year"]),
            "top_work": None if pd.isna(row["top_work"]) else str(row["top_work"]),
            "work_count": None if pd.isna(row["work_count"]) else int(row["work_count"]),
            "source": row["source"],
            "source_url": row["source_url"],
            "parser_version": row["parser_version"],
            "collected_at": row["collected_at"].to_pydatetime(),
        }
        try:
            valid_authors.append(AuthorContract(**payload))
        except ValidationError as exc:
            err = exc.errors()[0]
            contract_rejects.append({
                "book_key": None,
                "source": row.get("source"),
                "field_name": ".".join(str(x) for x in err["loc"]),
                "reason": f"AuthorContract: {err['msg']}",
                "title_raw": row.get("author_name"),
                "price_raw": None,
                "detail_url_raw": row.get("source_url"),
                "_collected_at": str(row.get("collected_at")),
                "_is_synthetic_dirty": False,
            })

    valid_author_keys = {a.author_key for a in valid_authors}

    for _, row in books_df.iterrows():
        if row["author_key"] not in valid_author_keys:
            contract_rejects.append({
                "book_key": row.get("book_key"),
                "source": row.get("source"),
                "field_name": "author_key",
                "reason": "Нарушение ссылочной целостности: author_key не прошёл AuthorContract",
                "title_raw": row.get("title"),
                "price_raw": str(row.get("price")),
                "detail_url_raw": row.get("source_url"),
                "_collected_at": str(row.get("collected_at")),
                "_is_synthetic_dirty": False,
            })
            continue

        payload = {
            "book_key": row["book_key"],
            "author_key": row["author_key"],
            "title": row["title"],
            "category": None if pd.isna(row["category"]) else str(row["category"]),
            "price": None if row["price"] is None or pd.isna(row["price"]) else Decimal(str(row["price"])),
            "stock": None if pd.isna(row["stock"]) else int(row["stock"]),
            "rating": None if pd.isna(row["rating"]) else int(row["rating"]),
            "in_stock": None if row["in_stock"] is None or pd.isna(row["in_stock"]) else bool(row["in_stock"]),
            "publish_year": None if pd.isna(row["publish_year"]) else int(row["publish_year"]),
            "isbn": None if pd.isna(row["isbn"]) else str(row["isbn"]),
            "source": row["source"],
            "source_url": row["source_url"],
            "parser_version": row["parser_version"],
            "collected_at": row["collected_at"].to_pydatetime(),
        }
        try:
            valid_books.append(BookContract(**payload))
        except ValidationError as exc:
            err = exc.errors()[0]
            contract_rejects.append({
                "book_key": row.get("book_key"),
                "source": row.get("source"),
                "field_name": ".".join(str(x) for x in err["loc"]),
                "reason": f"BookContract: {err['msg']}",
                "title_raw": row.get("title"),
                "price_raw": str(row.get("price")),
                "detail_url_raw": row.get("source_url"),
                "_collected_at": str(row.get("collected_at")),
                "_is_synthetic_dirty": False,
            })

    return valid_authors, valid_books, contract_rejects


def run_contract_probes(sample_book: BookContract) -> pd.DataFrame:
    """Демонстрация работы контракта на 6 заведомо битых записях (раздел 5.4)."""
    base: dict[str, Any] = sample_book.model_dump()
    probes = [
        ("1. Отрицательная цена (price = -15.00)", {"price": Decimal("-15.00")}),
        ("2. Рейтинг вне диапазона 1..5 (rating = 7)", {"rating": 7}),
        ("3. Пустой заголовок после обрезки (title = ' \xa0 ')", {"title": "   "}),
        ("4. Наивная дата без таймзоны UTC", {"collected_at": datetime(2026, 11, 19, 12, 0)}),
        ("5. URL без схемы http(s)://", {"source_url": "books.toscrape.com/item/1"}),
        ("6. Несогласованность: in_stock=True при stock=0", {"in_stock": True, "stock": 0}),
    ]
    results = []
    for label, patch in probes:
        try:
            BookContract(**{**base, **patch})
            results.append({"проверка": label, "результат": "ПРОПУЩЕНА (ошибка)", "сообщение об ошибке": ""})
        except ValidationError as exc:
            err = exc.errors()[0]
            loc = ".".join(str(x) for x in err["loc"]) or "model"
            results.append({
                "проверка": label,
                "результат": "отклонена",
                "сообщение об ошибке": f"{loc}: {err['msg']}",
            })
    return pd.DataFrame(results)
