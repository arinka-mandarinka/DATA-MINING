# Итоговый проект: гибридный пайплайн сбора данных (books.toscrape.com + Open Library)

## Исследовательский вопрос
Как цена и рейтинг книг в каталоге `books.toscrape.com` соотносятся с их библиографическими характеристиками (годом первого издания и числом изданий) по данным Open Library в разрезе категорий?

## Быстрый старт (воспроизведение с нуля)

1. Создайте виртуальное окружение и установите зависимости:
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

2. Полный прогон пайплайна (ожидаемое время: ~3–5 минут):
```bash
python -m src.run_pipeline
```

3. Демонстрация идемпотентности (три прогона подряд):
```bash
python -m src.run_pipeline --incremental
python -m src.run_pipeline --incremental
```

4. Демонстрация устойчивости (искусственный отказ `httpbin.org/status/503` и срабатывание Circuit Breaker):
```bash
python -m src.run_pipeline --simulate-failure
```

5. Аналитика и графики:
```bash
jupyter notebook analysis.ipynb
```
