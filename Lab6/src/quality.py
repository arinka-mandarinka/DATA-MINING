"""Расчёт метрик качества данных, светофор и блокировка загрузки в mart при красном статусе."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


class QualityGateError(RuntimeError):
    """Исключение при красном статусе светофора качества данных."""


THRESHOLDS: dict[str, tuple[float, float]] = {
    "1. Полнота обязательных полей, %": (100.0, 99.0),
    "2. Полнота необязательных полей, %": (55.0, 35.0),
    "3. Уникальность бизнес-ключа, %": (100.0, 99.0),
    "4. Валидность (доля от сырых записей), %": (90.0, 75.0),
    "5. Доля отбраковки, %": (5.0, 15.0),
    "6. Согласованность бизнес-правил, %": (100.0, 98.0),
    "7. Актуальность данных (возраст), ч": (48.0, 168.0),
    "8. Отклонение объёма от прошлого прогона, %": (10.0, 25.0),
}

LOWER_IS_BETTER = {
    "5. Доля отбраковки, %",
    "7. Актуальность данных (возраст), ч",
    "8. Отклонение объёма от прошлого прогона, %",
}

JUSTIFICATION = {
    "1. Полнота обязательных полей, %": "Ключ, название, автор и метаданные должны быть заполнены на 100%",
    "2. Полнота необязательных полей, %": "В Open Library нет цен/остатков, в HTML нет ISBN/года (норма >= 55%)",
    "3. Уникальность бизнес-ключа, %": "После дедупликации дубли по book_key недопустимы",
    "4. Валидность (доля от сырых записей), %": "С учётом ~6.6% синтетического шума и дублей норма >= 90%",
    "5. Доля отбраковки, %": "Порог <= 5% для штатного сбора; > 15% сигнализирует о поломке парсера",
    "6. Согласованность бизнес-правил, %": "После фильтрации и контракта нарушений быть не должно (100%)",
    "7. Актуальность данных (возраст), ч": "Свежесть последнего сбора относительно момента проверки",
    "8. Отклонение объёма от прошлого прогона, %": "Защита от резкой деградации объёма выборки между запусками",
}


def evaluate_quality(
    raw_df: pd.DataFrame,
    books_df: pd.DataFrame,
    rejects_df: pd.DataFrame,
    prev_stats_path: Path | None = None,
    reference_now: datetime | None = None,
) -> tuple[pd.DataFrame, str]:
    """Вычисляет 8 метрик качества, определяет статус каждой и итоговый вердикт."""
    if reference_now is None:
        reference_now = datetime.now(timezone.utc)

    total_raw = max(len(raw_df), 1)
    total_clean = max(len(books_df), 1)

    mandatory_cols = ["book_key", "author_key", "title", "source", "source_url", "collected_at"]
    optional_cols = ["category", "price", "stock", "rating", "publish_year", "isbn"]

    mandatory_completeness = float(books_df[mandatory_cols].notna().all(axis=1).mean() * 100.0)
    optional_completeness = float(books_df[optional_cols].notna().mean().mean() * 100.0)
    uniqueness = float(books_df["book_key"].nunique() / total_clean * 100.0)
    validity_rate = float(len(books_df) / total_raw * 100.0)
    reject_rate = float(len(rejects_df) / total_raw * 100.0)

    # Согласованность бизнес-правил на чистом слое
    consistent_mask = (
        books_df["rating"].isna() | books_df["rating"].between(1, 5)
    ) & (
        books_df["stock"].isna() | (books_df["stock"] >= 0)
    ) & (
        books_df["publish_year"].isna() | books_df["publish_year"].between(1450, 2026)
    )
    rules_consistency = float(consistent_mask.mean() * 100.0)

    max_collected = pd.to_datetime(books_df["collected_at"], utc=True).max()
    age_hours = max(0.0, (reference_now - max_collected.to_pydatetime()).total_seconds() / 3600.0)

    prev_count = len(books_df)
    if prev_stats_path and prev_stats_path.exists():
        try:
            prev_data = json.loads(prev_stats_path.read_text(encoding="utf-8"))
            prev_count = int(prev_data.get("clean_books_count", len(books_df)))
        except (ValueError, KeyError):
            prev_count = len(books_df)

    volume_drift_pct = abs(len(books_df) - prev_count) / max(prev_count, 1) * 100.0

    values = {
        "1. Полнота обязательных полей, %": round(mandatory_completeness, 2),
        "2. Полнота необязательных полей, %": round(optional_completeness, 2),
        "3. Уникальность бизнес-ключа, %": round(uniqueness, 2),
        "4. Валидность (доля от сырых записей), %": round(validity_rate, 2),
        "5. Доля отбраковки, %": round(reject_rate, 2),
        "6. Согласованность бизнес-правил, %": round(rules_consistency, 2),
        "7. Актуальность данных (возраст), ч": round(age_hours, 2),
        "8. Отклонение объёма от прошлого прогона, %": round(volume_drift_pct, 2),
    }

    rows = []
    for metric, val in values.items():
        green_th, yellow_th = THRESHOLDS[metric]
        if metric in LOWER_IS_BETTER:
            status = "зелёный" if val <= green_th else ("жёлтый" if val <= yellow_th else "красный")
            th_label = f"<= {green_th} / <= {yellow_th}"
        else:
            status = "зелёный" if val >= green_th else ("жёлтый" if val >= yellow_th else "красный")
            th_label = f">= {green_th} / >= {yellow_th}"

        rows.append({
            "метрика": metric,
            "значение": val,
            "пороги (зелёный / жёлтый)": th_label,
            "статус": status,
            "обоснование порога": JUSTIFICATION[metric],
        })

    quality_df = pd.DataFrame(rows)
    statuses = set(quality_df["статус"].tolist())
    if "красный" in statuses:
        verdict = "НЕ ПРИГОДЕН (КРАСНЫЙ)"
    elif "жёлтый" in statuses:
        verdict = "ПРИГОДЕН С ЗАМЕЧАНИЯМИ (ЖЁЛТЫЙ)"
    else:
        verdict = "ПРИГОДЕН (ЗЕЛЁНЫЙ)"

    return quality_df, verdict


def enforce_quality_gate(quality_df: pd.DataFrame) -> None:
    """Прерывает загрузку в слой mart при наличии хотя бы одной метрики в красной зоне."""
    red_rows = quality_df[quality_df["статус"] == "красный"]
    if not red_rows.empty:
        reasons = "; ".join(
            f"{r['метрика']} = {r['значение']} (порог: {r['пороги (зелёный / жёлтый)']})"
            for _, r in red_rows.iterrows()
        )
        raise QualityGateError(f"Загрузка в слой mart ОСТАНОВЛЕНА светофором качества: {reasons}")
