"""Скрипт воспроизводимого запуска всех обязательных прогонов лабораторной работы.

Выполняет по очереди:
1. Демонстрацию HTTPCACHE (2 последовательных прогона -> stats_cache_1.json, stats_cache_2.json).
2. Демонстрацию JOBDIR (2 прогона с возобновлением -> jobstate/, stats_job_1.json, stats_job_2.json).
3. Основной прогон (350+ записей -> data/crawl.db, data/items.jsonl, data/stats_final.json).
4. Контрольный прогон для smoke-check (сравнение стабильности).
5. Запуск quality/smoke_check.py.
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
JOBSTATE = ROOT / "jobstate"


def run(cmd: list[str], title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\nКоманда: {' '.join(cmd)}\n{'=' * 72}")
    res = subprocess.run(cmd, cwd=ROOT)
    if res.returncode != 0:
        raise RuntimeError(f"Команда завершилась с кодом {res.returncode}: {' '.join(cmd)}")


def main() -> None:
    DATA.mkdir(exist_ok=True)
    py = sys.executable

    # --- 1. Демонстрация HTTPCACHE ---
    shutil.rmtree(ROOT / ".scrapy" / "httpcache", ignore_errors=True)
    cache_db = DATA / "cache_demo.db"
    cache_db.unlink(missing_ok=True)

    for idx in (1, 2):
        run(
            [
                py, "-m", "scrapy", "crawl", "books_categories",
                "-a", f"run_id=cache_{idx}",
                "-s", "HTTPCACHE_ENABLED=True",
                "-s", "CLOSESPIDER_ITEMCOUNT=40",
                "-s", f"SQLITE_DB_PATH={cache_db}",
            ],
            f"1.{idx}. Демонстрация HTTPCACHE (прогон {idx})",
        )

    c1 = json.loads((DATA / "stats_cache_1.json").read_text(encoding="utf-8"))
    c2 = json.loads((DATA / "stats_cache_2.json").read_text(encoding="utf-8"))
    print(
        f"\n[HTTPCACHE] Прогон 1: store={c1.get('httpcache/store', 0)}, "
        f"hit={c1.get('httpcache/hit', 0)}, time={c1.get('elapsed_time_seconds', 0):.2f}s"
    )
    print(
        f"[HTTPCACHE] Прогон 2: store={c2.get('httpcache/store', 0)}, "
        f"hit={c2.get('httpcache/hit', 0)}, time={c2.get('elapsed_time_seconds', 0):.2f}s"
    )
    cache_db.unlink(missing_ok=True)

    # --- 2. Демонстрация JOBDIR ---
    if JOBSTATE.exists():
        shutil.rmtree(JOBSTATE)
    JOBSTATE.mkdir(exist_ok=True)
    job_db = DATA / "job_demo.db"
    job_db.unlink(missing_ok=True)

    for idx in (1, 2):
        run(
            [
                py, "-m", "scrapy", "crawl", "books_categories",
                "-a", f"run_id=job_{idx}",
                "-s", f"JOBDIR={JOBSTATE}",
                "-s", "CLOSESPIDER_ITEMCOUNT=40",
                "-s", f"SQLITE_DB_PATH={job_db}",
            ],
            f"2.{idx}. Демонстрация JOBDIR (прогон {idx})",
        )

    j1 = json.loads((DATA / "stats_job_1.json").read_text(encoding="utf-8"))
    j2 = json.loads((DATA / "stats_job_2.json").read_text(encoding="utf-8"))
    print(f"\n[JOBDIR] Содержимое jobstate/: {sorted(p.name for p in JOBSTATE.iterdir())}")
    print(
        f"[JOBDIR] Прогон 1: items={j1.get('item_scraped_count', 0)}, "
        f"dupefilter/filtered={j1.get('dupefilter/filtered', 0)}"
    )
    print(
        f"[JOBDIR] Прогон 2: items={j2.get('item_scraped_count', 0)}, "
        f"dupefilter/filtered={j2.get('dupefilter/filtered', 0)}"
    )
    job_db.unlink(missing_ok=True)

    # --- 3. Итоговый прогон (350+ записей) ---
    final_db = DATA / "crawl.db"
    final_db.unlink(missing_ok=True)
    items_jsonl = DATA / "items.jsonl"
    items_jsonl.unlink(missing_ok=True)

    run(
        [
            py, "-m", "scrapy", "crawl", "books_categories",
            "-a", "run_id=final",
            "-O", str(items_jsonl),
        ],
        "3. Итоговый прогон (CLOSESPIDER_ITEMCOUNT=350 -> data/crawl.db + data/items.jsonl)",
    )

    # --- 4. Smoke-check (проверка полноты БД и сравнение с базовым прогоном) ---
    # Если ранее уже был сохранён stats_prev.json, сравниваем с ним; иначе проверяем БД и stats_final.json
    smoke_cmd = [
        py, str(ROOT / "quality" / "smoke_check.py"),
        "--current", str(DATA / "stats_final.json"),
        "--db", str(final_db),
    ]
    prev_stats = DATA / "stats_prev.json"
    if prev_stats.exists():
        smoke_cmd += ["--previous", str(prev_stats)]
    run(smoke_cmd, "4. Проверка качества (smoke_check.py)")

    print("\nГотово! Теперь можно открыть quality/quality_report.ipynb и выполнить Run All.")


if __name__ == "__main__":
    main()
