import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.clean import clean_books, clean_openlibrary, save_rejects
from src.collect_api import collect_openlibrary_api
from src.collect_html import collect_books_html
from src.observability import (
    CircuitBreakerOpenError,
    StructuredLogger,
    evaluate_alerts,
    utc_now_iso,
)
from src.quality import evaluate_quality
from src.storage import build_mart, init_db, record_pipeline_run, upsert_books, upsert_openlibrary


def main() -> int:
    parser = argparse.ArgumentParser(description="Hybrid Data Collection Pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--incremental", action="store_true", help="Fast incremental run")
    parser.add_argument(
        "--simulate-failure", action="store_true", help="Simulate 503 failure for demo"
    )
    parser.add_argument("--skip-api", action="store_true", help="Skip Open Library API stage")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started_at = utc_now_iso()
    t0 = time.perf_counter()

    log_path = Path(cfg["paths"]["logs_dir"]) / f"run_{run_id}.jsonl"
    reports_dir = Path(cfg["paths"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)
    logger = StructuredLogger(log_path, run_id)
    logger.log("pipeline_started", incremental=args.incremental, simulate_failure=args.simulate_failure)

    db_path = Path(cfg["paths"]["db_path"])
    init_db(db_path, Path(cfg["paths"]["schema_path"]))

    html_res = {"collected_records": 0, "skipped_304": 0, "http_metrics": {}}
    api_res = {"collected_records": 0, "http_metrics": {}}
    status = "SUCCESS"

    try:
        html_res = collect_books_html(
            cfg, run_id, logger, incremental=args.incremental, simulate_failure=args.simulate_failure
        )
        if not args.skip_api:
            api_res = collect_openlibrary_api(cfg, run_id, logger, incremental=args.incremental)
    except CircuitBreakerOpenError as exc:
        status = "DEGRADED_CIRCUIT_BREAKER"
        logger.log("pipeline_incident", level="ERROR", reason=str(exc))
    except Exception as exc:
        status = "FAILED"
        logger.log("pipeline_exception", level="ERROR", reason=str(exc))

    # Слой очистки и контракта
    raw_dir = Path(cfg["paths"]["raw_dir"])
    clean_dir = Path(cfg["paths"]["clean_dir"])
    books_df, books_rejects = clean_books(
        raw_dir / "books_raw.jsonl", clean_dir, cfg["project"]["parser_version"]
    )
    ol_df, ol_rejects = clean_openlibrary(
        raw_dir / "openlibrary_raw.jsonl", clean_dir, cfg["project"]["parser_version"]
    )
    all_rejects = books_rejects + ol_rejects
    save_rejects(all_rejects, clean_dir)

    # Идемпотентная загрузка (UPSERT)
    upsert_books(db_path, books_df)
    upsert_openlibrary(db_path, ol_df)

    # Метрики качества
    quality = evaluate_quality(
        db_path, books_df, ol_df, len(all_rejects), cfg["quality_thresholds"]
    )
    logger.log("quality_evaluated", verdict=quality["verdict"], checks=quality["checks"])

    mart_stats = {}
    if quality["verdict"] == "GREEN":
        mart_stats = build_mart(db_path, Path(cfg["paths"]["mart_dir"]))
        logger.log("mart_built", stats=mart_stats)
    else:
        logger.log("mart_blocked_red_quality", level="WARNING")

    # Сводные HTTP-метрики
    h_m = html_res.get("http_metrics") or {}
    a_m = api_res.get("http_metrics") or {}
    req_total = h_m.get("requests_total", 0) + a_m.get("requests_total", 0)
    err_total = h_m.get("errors_total", 0) + a_m.get("errors_total", 0)
    ret_total = h_m.get("retries_total", 0) + a_m.get("retries_total", 0)
    bytes_total = h_m.get("bytes_received", 0) + a_m.get("bytes_received", 0)
    merged_codes = {}
    for src_codes in (h_m.get("status_counts", {}), a_m.get("status_counts", {})):
        for k, v in src_codes.items():
            merged_codes[k] = merged_codes.get(k, 0) + v

    finished_at = utc_now_iso()
    duration_sec = round(time.perf_counter() - t0, 2)

    run_report = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": duration_sec,
        "status": status,
        "incremental": args.incremental,
        "http_summary": {
            "requests_total": req_total,
            "status_counts": merged_codes,
            "errors_total": err_total,
            "error_rate": round(err_total / req_total, 4) if req_total > 0 else 0.0,
            "retries_total": ret_total,
            "latency_median_ms": max(
                h_m.get("latency_median_ms", 0.0), a_m.get("latency_median_ms", 0.0)
            ),
            "latency_p95_ms": max(
                h_m.get("latency_p95_ms", 0.0), a_m.get("latency_p95_ms", 0.0)
            ),
            "bytes_received": bytes_total,
        },
        "sources": {
            "books_html": html_res,
            "openlibrary_api": api_res,
        },
        "records": {
            "collected_html": html_res.get("collected_records", 0),
            "collected_api": api_res.get("collected_records", 0),
            "clean": len(books_df),
            "rejected": len(all_rejects),
            "enriched": quality["enriched_books_count"],
        },
        "quality": quality,
        "mart": mart_stats,
    }

    alerts = evaluate_alerts(run_report, cfg["alert_thresholds"], reports_dir)
    run_report["alerts"] = alerts

    report_path = reports_dir / f"run_report_{run_id}.json"
    report_path.write_text(
        json.dumps(run_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    record_pipeline_run(
        db_path,
        {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "quality_verdict": quality["verdict"],
            "books_collected": html_res.get("collected_records", 0),
            "books_clean": len(books_df),
            "books_rejected": len(all_rejects),
            "api_enriched": quality["enriched_books_count"],
        },
    )

    logger.log("pipeline_finished", status=status, duration_sec=duration_sec, alerts=alerts)
    print(json.dumps(run_report, ensure_ascii=False, indent=2))

    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
