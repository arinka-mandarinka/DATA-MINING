# Лабораторная работа № 5 — Промышленный краулинг (Scrapy)

**Студент:** Мурашова Арина  ПМИ-3
**Вариант:** 2 — `books.toscrape.com` (обход по 50 категориям)

## Структура проекта

```
lab5/
├── scrapy.cfg
├── books_crawler/
│   ├── items.py                 # схема записи BookItem
│   ├── pipelines.py             # 4 звена пайплайна (100, 200, 300, 400)
│   ├── settings.py              # вежливые настройки и предохранители с комментариями
│   ├── middlewares.py
│   └── spiders/
│       └── books_categories.py  # двухуровневый обход по категориям с cb_kwargs и errback
├── data/                        # crawl.db, items.jsonl, stats_*.json (создаются при запуске)
├── jobstate/                    # состояние JOBDIR (создаётся при демонстрации)
├── quality/
│   ├── quality_report.ipynb     # расчёт полноты, агрегаты и графики по реальным данным
│   └── smoke_check.py           # проверка деградации между прогонами (>20% падения / >5% пустых)
├── nocode/                      # n8n-workflow
├── run_all.py                   # автоматический прогон всех этапов
├── requirements.txt
└── README.md
```

## Установка зависимостей

```bash
python -m venv .venv
# Linux / macOS / WSL:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

## Быстрый запуск всех этапов

Из корня `lab5_murashova_2/`:

```bash
python run_all.py
```

Скрипт последовательно выполнит демонстрацию `HTTPCACHE`, демонстрацию `JOBDIR`, основной прогон на 350+ записей (с записью в `data/crawl.db`, `data/items.jsonl` и `data/stats_final.json`) и проверку `quality/smoke_check.py`.

