# Лабораторная работа № 6. Хранение, очистка и структурирование собранных данных

**Студент:** Мурашова Арина  
**Вариант:** 2 — *«Книги и авторы»* (HTML-каталог книг + API книжного сервиса → две связанные таблицы `authors` и `books` со связью по `author_key`)

## Структура проекта

```text
Lab6/
├── raw/
│   ├── books_toscrape.jsonl          # HTML-источник (лаб. 2 + добор), append-only
│   └── openlibrary_api.jsonl         # API-источник (Open Library REST API), append-only
├── clean/
│   ├── books.parquet                 # чистый слой: таблица книг (генерируется rebuild.py)
│   ├── authors.parquet               # чистый слой: таблица авторов (генерируется rebuild.py)
│   ├── rejects.jsonl                 # отбракованные записи с указанием поля и причины
│   └── format_benchmark.csv          # результаты сравнения CSV, JSON Lines и Parquet
├── mart/
│   ├── views/
│   │   ├── category_summary.parquet      # витрина 1: агрегаты по категориям
│   │   ├── author_stats.parquet          # витрина 2: статистика по авторам (JOIN)
│   │   └── price_rating_bands.parquet    # витрина 3: ценовые сегменты и рейтинги
│   └── snapshots/                        # партиционированные снапшоты по дате сбора
├── db/
│   ├── schema.sql                    # DDL-схема SQLite (PK, FK, NOT NULL, CHECK, индексы)
│   ├── warehouse.db                  # база данных SQLite после 3 прогонов загрузки
│   └── load.py                       # идемпотентный UPSERT (executemany) и тесты ограничений
├── src/
│   ├── collect.py                    # сбор/добор новых данных в raw/ (режим дозаписи)
│   ├── rebuild.py                    # полная пересборка clean/, db/ и mart/ из raw/
│   ├── clean.py                      # 6 шагов очистки с протоколом
│   ├── contract.py                   # модели Pydantic v2 + тесты на 6 битых записях
│   └── quality.py                    # 8 метрик качества, пороги и «светофор»
├── quality_report.ipynb              # блокнот с протоколом, метриками и графиками
├── nocode/
│   ├── workflow.json                 # экспорт workflow для n8n
│   └── nocode_result.csv             # результат выгрузки подмножества
├── requirements.txt
└── README.md
```

## Воспроизведение с нуля

1. Установить зависимости:
   ```bash
   pip install -r requirements.txt
   ```
2. Запустить полную пересборку хранилища из неизменяемого слоя `raw/` (без обращения к сети):
   ```bash
   python src/rebuild.py
   ```
   Скрипт создаст `clean/books.parquet`, `clean/authors.parquet`, `clean/rejects.jsonl`, `clean/format_benchmark.csv`, обновит `db/warehouse.db` (выполнив 3 прогона UPSERT и проверки ограничений) и сформирует витрины и партиции в `mart/`.
3. Открыть и при необходимости перезапустить блокнот `quality_report.ipynb`:
   ```bash
   jupyter notebook quality_report.ipynb
   ```
4. **No-code часть (n8n):**
   - Импортировать файл `nocode/workflow.json` в интерфейс n8n (*Workflows → Import from File*).
