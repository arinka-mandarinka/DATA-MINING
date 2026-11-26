# Паспорт данных (Data Card)

* **Название датасета:** `books_openlibrary_warehouse`
* **Происхождение:** учебный каталог `books.toscrape.com` + публичный API `openlibrary.org`.
* **Частота обновления:** 1 раз в сутки (инкрементальный режим через n8n).
* **Основные таблицы:**
  * `books` — `book_key`, `title`, `title_norm`, `category`, `price_gbp`, `rating`, `in_stock`, `availability_text`, `product_url`, служебные поля (`source`, `collected_at`, `updated_at`, `parser_version`).
  * `openlibrary_enrichment` — `title_norm`, `ol_work_key`, `ol_title`, `author_name`, `first_publish_year`, `edition_count`, `isbn`, `language`, `match_score`, служебные поля.
  * `mart_books_enriched` / `mart_category_summary` — аналитические витрины.
* **Известные ограничения:** синтетический характер цен/рейтингов в `books.toscrape.com`; неполное покрытие при сопоставлении по названию (`match_score >= 0.6`).
* **Лицензия/условия:** `books.toscrape.com` предназначен для учебного парсинга; метаданные Open Library распространяются под CC0 / публичным доступом Internet Archive.
