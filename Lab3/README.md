# Лабораторная работа 3 — сбор данных через GitHub REST API (Python + n8n)

Вариант: GitHub REST — issues репозитория (Link-пагинация) + обогащение данными о репозитории и контрибьюторах.
Задача анализа: динамика открытия/закрытия issue, время жизни задачи.

## Воспроизведение

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # вписать GITHUB_TOKEN (необязательно)
jupyter notebook lab3.ipynb        # Kernel → Restart & Run All
```

Переменные окружения:

| `GITHUB_TOKEN` | необязательно | Без токена лимит 60 запросов/час, одного полного прогона (~16 запросов) хватает. С токеном — 5000/час. Токен читается только из окружения / `.env`, в блокноте выводится маскированным. |


## Что получается

| Файл | Содержимое |
|---|---|
| `data_raw.jsonl` | сырые объекты из ответов API, одна строка = один объект + `endpoint`, `repo`, `run_id`, `fetched_at` (append-only) |
| `data_clean.csv` | итоговая таблица issue (без PR), нормализованная и обогащённая |
| `issue_labels.csv` | метки issue в длинном формате (результат `record_path`) |
| `collection_log.csv` | журнал: каждая попытка HTTP-запроса — страница, URL, параметры, код, число записей, длительность, остаток квоты, ожидание, ошибка |
| `state.json` | водяной знак `updated_at` по каждому репозиторию |
| `nocode/workflow.json` | экспорт n8n (импорт: Workflows → Import from File) |
| `nocode/n8n_algorithm.md` | описание no-code сценария и чек-лист метрик |

## Инкрементальность

Повторный запуск блокнота (или раздела 9) запрашивает только issue с `updated_at` позже водяного знака (`since=…&direction=asc`). Сразу после первого прогона новых записей 0, строки в `data_clean.csv` не дублируются.
