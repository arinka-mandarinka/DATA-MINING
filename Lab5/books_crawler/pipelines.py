"""Item Pipeline из четырёх звеньев (100, 200, 300, 400).

Основано на шаблоне занятия 5:
- современный API Scrapy (методы без аргумента spider, доступ к статистике через from_crawler);
- каждый DropItem учитывается в статистике через stats.inc_value;
- соединение с SQLite открывается один раз в open_spider и закрывается в close_spider;
- запись в БД выполняется пакетами (executemany) с INSERT OR REPLACE.
"""

from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem

RATING_MAP = {
    "One": 1,
    "Two": 2,
    "Three": 3,
    "Four": 4,
    "Five": 5,
}


class StatsMixin:
    """Доступ к статистике прогона и настройкам через from_crawler."""

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def __init__(self, crawler):
        self.crawler = crawler

    def count(self, key: str, n: int = 1) -> None:
        self.crawler.stats.inc_value(key, n)


class ValidationPipeline(StatsMixin):
    """Звено 100 — проверка наличия обязательных полей."""

    REQUIRED = (
        "upc",
        "title",
        "price_excl_tax_raw",
        "availability_raw",
        "reviews_raw",
        "category",
        "category_from_menu",
        "url",
    )

    def process_item(self, item):
        adapter = ItemAdapter(item)
        missing = [
            field for field in self.REQUIRED
            if adapter.get(field) is None or str(adapter.get(field)).strip() == ""
        ]
        if missing:
            self.count("dropped/missing_fields")
            for field in missing:
                self.count(f"dropped/missing_field/{field}")
            raise DropItem(f"Отсутствуют обязательные поля: {missing}")
        return item


class CleanPipeline(StatsMixin):
    """Звено 200 — очистка, обрезка пробелов, нормализация регистра и приведение типов."""

    @staticmethod
    def _parse_price(raw_value: str | None) -> float:
        cleaned = re.sub(r"[^\d.]", "", raw_value or "")
        if not cleaned:
            raise ValueError(f"Пустая цена: {raw_value!r}")
        value = float(cleaned)
        if value < 0:
            raise ValueError(f"Отрицательная цена: {value}")
        return round(value, 2)

    @staticmethod
    def _normalize_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = re.sub(r"\s+", " ", value).strip()
        return normalized if normalized else None

    def process_item(self, item):
        adapter = ItemAdapter(item)

        # Обрезка пробелов и нормализация регистра
        adapter["upc"] = self._normalize_text(adapter.get("upc")).lower()
        adapter["title"] = self._normalize_text(adapter.get("title"))
        adapter["category"] = self._normalize_text(adapter.get("category"))
        adapter["category_from_menu"] = self._normalize_text(adapter.get("category_from_menu"))
        adapter["description"] = self._normalize_text(adapter.get("description"))

        # Цены -> float
        try:
            adapter["price_excl_tax"] = self._parse_price(adapter.get("price_excl_tax_raw"))
            price_source = adapter.get("price_raw") or adapter.get("list_price_raw") or adapter.get("price_excl_tax_raw")
            adapter["price"] = self._parse_price(price_source)
            adapter["price_incl_tax"] = (
                self._parse_price(adapter.get("price_incl_tax_raw"))
                if adapter.get("price_incl_tax_raw")
                else adapter["price"]
            )
            adapter["tax"] = (
                self._parse_price(adapter.get("tax_raw"))
                if adapter.get("tax_raw")
                else 0.0
            )
        except ValueError as exc:
            self.count("dropped/bad_price")
            raise DropItem(f"Некорректная цена: {exc}") from exc

        # Наличие -> int
        avail_raw = adapter.get("availability_raw") or ""
        stock_match = re.search(r"(\d+)", avail_raw)
        if stock_match:
            adapter["stock"] = int(stock_match.group(1))
        elif "out of stock" in avail_raw.lower():
            adapter["stock"] = 0
        else:
            self.count("dropped/bad_stock")
            raise DropItem(f"Некорректное значение наличия: {avail_raw!r}")

        # Число отзывов -> int
        try:
            adapter["reviews"] = int(str(adapter.get("reviews_raw")).strip())
            if adapter["reviews"] < 0:
                raise ValueError("Отрицательное число отзывов")
        except (TypeError, ValueError) as exc:
            self.count("dropped/bad_reviews")
            raise DropItem(f"Некорректное число отзывов: {adapter.get('reviews_raw')!r}") from exc

        # Рейтинг -> int (1..5)
        rating_tokens = (adapter.get("rating_raw") or "").split()
        adapter["rating"] = RATING_MAP.get(rating_tokens[-1]) if rating_tokens else None

        # Дата сбора -> ISO-8601
        raw_dt = adapter.get("scraped_at")
        try:
            if raw_dt:
                parsed_dt = datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00"))
            else:
                parsed_dt = datetime.now(timezone.utc)
            adapter["scraped_at"] = parsed_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        except ValueError as exc:
            self.count("dropped/bad_date")
            raise DropItem(f"Некорректная дата: {raw_dt!r}") from exc

        # Удаляем сырые промежуточные поля, чтобы выгрузка была чистой
        for raw_field in (
            "price_raw",
            "price_excl_tax_raw",
            "price_incl_tax_raw",
            "tax_raw",
            "list_price_raw",
            "availability_raw",
            "rating_raw",
            "reviews_raw",
        ):
            adapter.pop(raw_field, None)

        return item


class DedupPipeline(StatsMixin):
    """Звено 300 — дедупликация по бизнес-ключу (UPC), а не по URL."""

    def open_spider(self):
        self.seen: set[str] = set()

    def process_item(self, item):
        adapter = ItemAdapter(item)
        key = adapter["upc"]
        if key in self.seen:
            self.count("dropped/duplicate")
            raise DropItem(f"Дубликат по бизнес-ключу UPC: {key}")
        self.seen.add(key)
        return item


class SQLitePipeline(StatsMixin):
    """Звено 400 — пакетная запись в SQLite (INSERT OR REPLACE)."""

    def open_spider(self):
        db_path = Path(self.crawler.settings.get("SQLITE_DB_PATH", "data/crawl.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.batch_size = int(self.crawler.settings.get("SQLITE_BATCH_SIZE", 25))
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
                upc                TEXT PRIMARY KEY,
                title              TEXT NOT NULL,
                price              REAL NOT NULL,
                price_excl_tax     REAL NOT NULL,
                price_incl_tax     REAL,
                tax                REAL,
                stock              INTEGER NOT NULL,
                rating             INTEGER,
                reviews            INTEGER NOT NULL,
                category           TEXT NOT NULL,
                category_from_menu TEXT NOT NULL,
                description        TEXT,
                url                TEXT NOT NULL,
                list_page          TEXT,
                scraped_at         TEXT NOT NULL
            )
            """
        )
        self.conn.commit()
        self.buffer: list[tuple] = []

    def process_item(self, item):
        adapter = ItemAdapter(item)
        self.buffer.append(
            (
                adapter["upc"],
                adapter["title"],
                adapter["price"],
                adapter["price_excl_tax"],
                adapter.get("price_incl_tax"),
                adapter.get("tax"),
                adapter["stock"],
                adapter.get("rating"),
                adapter["reviews"],
                adapter["category"],
                adapter["category_from_menu"],
                adapter.get("description"),
                adapter["url"],
                adapter.get("list_page"),
                adapter["scraped_at"],
            )
        )
        if len(self.buffer) >= self.batch_size:
            self._flush()
        return item

    def _flush(self) -> None:
        if not self.buffer:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO books (
                upc, title, price, price_excl_tax, price_incl_tax, tax,
                stock, rating, reviews, category, category_from_menu,
                description, url, list_page, scraped_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self.buffer,
        )
        self.conn.commit()
        self.count("db/rows_written", len(self.buffer))
        self.buffer.clear()

    def close_spider(self):
        self._flush()
        self.conn.close()
