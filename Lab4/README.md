# Лабораторная работа 4, вариант 2 — quotes.toscrape.com

**Источник (динамика):** `https://quotes.toscrape.com/js/` и `https://quotes.toscrape.com/js-delayed/`
**Контрольный статический источник:** `https://quotes.toscrape.com/`

## Как воспроизвести

### Вариант 1 — Jupyter Notebook

1. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   python -m playwright install chromium
Откройте lab4.ipynb в Jupyter Notebook / JupyterLab.

Выполните Run All (последовательно сверху вниз).

Первая ячейка устанавливает playwright, psutil, pandas, beautifulsoup4, requests и Chromium.

Полный прогон занимает ≈ 4–6 минут. Большую часть времени забирает задержка отрисовки /js-delayed/ (около 10 секунд на страницу) и паузы вежливости.

Все результаты (CSV, артефакты, журналы) появятся в корневой папке рядом с блокнотом.

Вариант 2 — локальный скрипт (опционально)
bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS/Linux

pip install -r requirements.txt
python -m playwright install chromium

python collect.py                        # обе площадки, 200 записей
python collect.py --headed               # видимое окно браузера
python collect.py --source js-delayed --max-items 30 --min-items 30
echo %ERRORLEVEL%                        # Windows
echo $?                                  # macOS/Linux
Коды возврата collect.py:

Код	Значение
0	успех
1	непредвиденная ошибка
2	записей меньше --min-items
3	smoke-check не пройден (> 5 % пустых text/author)
4	были таймауты (флаг --strict)
No-code
Упрощённый сценарий реализован в Octoparse. См. папку nocode/:

nocode_result.csv — результат сбора (100 уникальных строк после удаления дублей);

screenshot_*.png — скриншоты интерфейса Octoparse (автоопределение полей, пагинация, экспорт);


Состав
├── lab4.ipynb                  # основной блокнот (диагностика, Playwright, дешёвая альтернатива, метрики)
├── collect.py                  # синхронный CLI-скрипт (резервный запуск)
├── data_browser.csv            # Playwright, 200 записей
├── data_light.csv              # requests + JSON из <script>, 200 записей
├── collection_log.csv          # журнал итераций браузера
├── collection_log_light.csv    # журнал запросов лёгкого способа
├── diagnostics_protocol.csv    # протокол диагностики источника
├── reconciliation_summary.csv  # сводка сверки наборов
├── discrepancies.csv           # детали расхождений
├── blocking_effect.csv         # эффект блокировки ресурсо
├── metrics_comparison.csv      # таблица сравнения, включая No-code
├── quality_report.csv          # проверка качества данных
├── locators_table.csv          # таблица локаторов с обоснованием
├── resilience_matrix.csv       # матрица устойчивости к редизайну
├── artifacts/                  # скриншоты/HTML сбоев, скриншоты с выключенным JS, failure_demo_log.csv
├── auth_state.json             # учебная сессия (вход один раз, student/student)
├── nocode/                     # Octoparse: результат, скриншоты, экспорт задачи
├── report.md                   # отчёт
├── requirements.txt
└── README.md
Источники и этика
Используются исключительно тренировочные площадки quotes.toscrape.com. Между запросами выдерживаются паузы вежливости (0.5 с). Сбор с коммерческих сайтов, обход капчи и антибот-защиты не производился. Персональные данные (152-ФЗ) в ходе работы не обрабатывались.

Воспроизводимость
Браузерный сбор: lab4.ipynb (основной) или collect.py (резерв).

Дешёвая альтернатива: встроена в lab4.ipynb (раздел 5).

No-code: экспорт задачи Octoparse (nocode/workflow.json) + nocode_result.csv.

Отчёт: report.md.
