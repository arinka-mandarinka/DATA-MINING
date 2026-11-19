"""Паук для варианта 2: books.toscrape.com — обход по 50 категориям.

Архитектура обхода:
- Уровень 0 (parse): главная страница -> извлечение 50 категорий из бокового меню.
- Уровень 1 (parse_category): страница категории -> карточки книг + пагинация внутри категории.
- Уровень 2 (parse_item): карточка книги -> детальные поля + контекст из уровня 1 через cb_kwargs.

Вся обработка/очистка/валидация данных вынесена в Item Pipeline (pipelines.py).
"""

from datetime import datetime, timezone
import json
from pathlib import Path

import scrapy
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.internet.error import DNSLookupError, TCPTimedOutError, TimeoutError

from books_crawler.items import BookItem


class BooksCategoriesSpider(scrapy.Spider):
    name = "books_categories"
    allowed_domains = ["books.toscrape.com"]
    start_urls = ["https://books.toscrape.com/"]

    def __init__(self, run_id: str = "1", stats_dir: str = "data", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.run_id = str(run_id)
        self.stats_dir = Path(stats_dir)

    def start_requests(self):
        """Явная отправка стартовых URL с dont_filter=False для корректной работы dupefilter при JOBDIR."""
        for url in self.start_urls:
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                dont_filter=False,
                priority=100,
            )

    def parse(self, response):
        """Точка входа: обход меню из 50 категорий."""
        category_links = response.css("div.side_categories ul li ul li a")
        self.crawler.stats.inc_value("crawl/categories_discovered", len(category_links))

        for link in category_links:
            href = link.attrib.get("href")
            category_name = (link.css("::text").get() or "").strip()
            if not href:
                continue
            yield response.follow(
                href,
                callback=self.parse_category,
                errback=self.handle_error,
                cb_kwargs={"category_from_menu": category_name},
            )

    def parse_category(self, response, category_from_menu: str):
        """Уровень 1: страница списка категории + пагинация внутри категории."""
        self.crawler.stats.inc_value("crawl/list_pages_visited")

        for book in response.css("article.product_pod"):
            href = book.css("h3 a::attr(href)").get()
            if not href:
                continue
            list_price_raw = book.css("p.price_color::text").get()
            yield response.follow(
                href,
                callback=self.parse_item,
                errback=self.handle_error,
                cb_kwargs={
                    "category_from_menu": category_from_menu,
                    "list_price_raw": list_price_raw,
                    "list_page": response.url,
                },
            )

        next_page = response.css("ul.pager li.next a::attr(href)").get()
        if next_page:
            yield response.follow(
                next_page,
                callback=self.parse_category,
                errback=self.handle_error,
                cb_kwargs={"category_from_menu": category_from_menu},
            )

    def parse_item(self, response, category_from_menu: str, list_price_raw: str | None, list_page: str):
        """Уровень 2: карточка книги. Только извлечение сырых полей без очистки."""
        table = {}
        for row in response.css("table.table-striped tr"):
            key = (row.css("th::text").get() or "").strip()
            val = (row.css("td::text").get() or "").strip()
            if key:
                table[key] = val

        # Семантический выбор категории из хлебных крошек (последняя ссылка <a> перед active)
        breadcrumb_links = [
            t.strip() for t in response.css("ul.breadcrumb li a::text").getall() if t.strip()
        ]
        category_breadcrumb = breadcrumb_links[-1] if len(breadcrumb_links) >= 3 else None

        # Безопасное извлечение класса рейтинга (без падения при отсутствии элемента)
        rating_el = response.css("div.product_main p.star-rating")
        rating_raw = rating_el.attrib.get("class") if rating_el else None

        # Семантический селектор описания через id="product_description" (без индексного XPath от корня)
        description = response.xpath(
            "//div[@id='product_description']/following-sibling::p[1]/text()"
        ).get()

        item = BookItem(
            upc=table.get("UPC"),
            title=response.css("div.product_main h1::text").get(),
            category=category_breadcrumb,
            category_from_menu=category_from_menu,
            description=description,
            price_raw=response.css("div.product_main p.price_color::text").get(),
            price_excl_tax_raw=table.get("Price (excl. tax)"),
            price_incl_tax_raw=table.get("Price (incl. tax)"),
            tax_raw=table.get("Tax"),
            list_price_raw=list_price_raw,
            availability_raw=table.get("Availability"),
            rating_raw=rating_raw,
            reviews_raw=table.get("Number of reviews"),
            url=response.url,
            list_page=list_page,
            scraped_at=datetime.now(timezone.utc).isoformat(),
        )
        yield item

    def handle_error(self, failure):
        """Обработка сетевых и HTTP-ошибок конкретного запроса с записью в статистику."""
        request = failure.request
        self.crawler.stats.inc_value("errback/total")

        if failure.check(HttpError):
            response = failure.value.response
            self.crawler.stats.inc_value(f"errback/http_{response.status}")
            self.logger.warning("HttpError %s на %s", response.status, response.url)
        elif failure.check(DNSLookupError):
            self.crawler.stats.inc_value("errback/dns_error")
            self.logger.warning("DNSLookupError на %s", request.url)
        elif failure.check(TimeoutError, TCPTimedOutError):
            self.crawler.stats.inc_value("errback/timeout_error")
            self.logger.warning("TimeoutError на %s", request.url)
        else:
            self.crawler.stats.inc_value("errback/other")
            self.logger.warning("Ошибка запроса %s: %r", request.url, failure.value)

    def closed(self, reason):
        """Сохранение статистики прогона в data/stats_<номер прогона>.json."""
        self.stats_dir.mkdir(parents=True, exist_ok=True)
        raw_stats = self.crawler.stats.get_stats()

        finish_time = datetime.now(timezone.utc)
        start_time = raw_stats.get("start_time")
        elapsed = raw_stats.get("elapsed_time_seconds")
        if elapsed is None and isinstance(start_time, datetime):
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            elapsed = round((finish_time - start_time).total_seconds(), 2)

        stats = {
            k: (v if isinstance(v, (int, float, bool)) else str(v))
            for k, v in raw_stats.items()
        }
        stats["finish_time"] = str(finish_time)
        if elapsed is not None:
            stats["elapsed_time_seconds"] = elapsed
        stats["close_reason"] = reason
        stats["run_id"] = self.run_id

        out_path = self.stats_dir / f"stats_{self.run_id}.json"
        out_path.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.logger.info("Статистика прогона сохранена в %s", out_path)
