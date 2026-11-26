# Архитектура пайплайна и паспорта источников

## Схема пайплайна
```text
   [src/run_pipeline.py (CLI / расписание)]
      ├──► src/collect_html.py (books.toscrape.com, BS4, ETag/304, Circuit Breaker)
      │        └──► raw/books_raw.jsonl (append-only)
      ├──► src/collect_api.py (openlibrary.org/search.json, watermark)
      │        └──► raw/openlibrary_raw.jsonl (append-only)
      ├──► src/clean.py + src/contract.py (Pydantic, 3 validators)
      │        ├──► clean/books_clean.parquet, clean/openlibrary_clean.parquet
      │        └──► clean/rejects.jsonl
      ├──► src/storage.py (SQLite UPSERT -> db/warehouse.db)
      ├──► src/quality.py (6 metrics -> GREEN/RED gate)
      │        └──► [if GREEN] mart/ (views + parquet/csv)
      └──► src/observability.py
               ├──► logs/run_<id>.jsonl
               └──► reports/run_report_<id>.json
                             ▲
                             │ (HTTP GET)
[n8n Schedule Trigger (03:00)] ──► [Fetch Run Report] ──► [IF Quality GREEN] ──► [Success / Alert]

Протокол диагностики HTML-источника (Занятие 4)
Источник: https://books.toscrape.com/

View Source: все карточки книг (article.product_pod), названия, цены, рейтинги и ссылки присутствуют непосредственно в исходном HTML.

Network/XHR: динамические XHR-запросы для рендера каталога отсутствуют.

Вывод: браузерная автоматизация (Playwright/Selenium) не требуется; достаточно requests + BeautifulSoup.

Паспорта источников
1. books.toscrape.com (HTML)
Тип: статический HTML-каталог.

Точка входа: https://books.toscrape.com/.

Способ пагинации: обход по 50 категориям и постраничный переход по ссылке li.next a.

Формат ответа: HTML (UTF-8, сжатие gzip).

Лимиты: rate_limit_sec=0.35, джиттер 0.15, robots.txt отсутствует (404), честный User-Agent с контактом.

Ключ уникальности: book_key (SHA-256 от product_url).

Персональные данные: отсутствуют.

2. Open Library Search API (REST API)
Тип: публичный REST API без регистрации.

Точка входа: https://openlibrary.org/search.json.

Способ пагинации: постраничный обход не требуется — выполняется точечный поиск по каждому уникальному названию (title, limit=1) с инкрементальным батчингом (max_titles_per_run) и фиксацией прогресса в state/watermark.json.

Формат ответа: JSON (gzip).

Лимиты: rate_limit_sec=0.5, джиттер 0.2, честный User-Agent с контактом.

Ключ уникальности: title_norm (связь с таблицей books).

Персональные данные: отсутствуют персональные данные пользователей (только публичные библиографические имена авторов книг).