import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set

from src.collect_html import load_watermark, save_watermark
from src.observability import (
    CircuitBreakerOpenError,
    PoliteHttpClient,
    StructuredLogger,
    utc_now_iso,
)


def normalize_title(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", title.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_unique_titles(books_raw_path: Path) -> List[Dict[str, str]]:
    seen: Set[str] = set()
    result: List[Dict[str, str]] = []
    if not books_raw_path.exists():
        return result

    with books_raw_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                title = obj.get("payload", {}).get("title", "").strip()
                if not title:
                    continue
                t_norm = normalize_title(title)
                if t_norm and t_norm not in seen:
                    seen.add(t_norm)
                    result.append({"title": title, "title_norm": t_norm})
            except json.JSONDecodeError:
                continue
    return result


def collect_openlibrary_api(
    cfg: Dict[str, Any],
    run_id: str,
    logger: StructuredLogger,
    incremental: bool = False,
) -> Dict[str, Any]:
    api_cfg = cfg["sources"]["openlibrary_api"]
    books_raw_path = Path(cfg["paths"]["raw_dir"]) / "books_raw.jsonl"
    api_raw_path = Path(cfg["paths"]["raw_dir"]) / "openlibrary_raw.jsonl"
    api_raw_path.parent.mkdir(parents=True, exist_ok=True)

    etags_path = Path(cfg["paths"]["state_dir"]) / "etags.json"
    watermark_path = Path(cfg["paths"]["state_dir"]) / "watermark.json"
    watermark = load_watermark(watermark_path)

    processed_titles = set(watermark.get("ol_processed_titles", []))
    all_titles = extract_unique_titles(books_raw_path)

    to_fetch = [t for t in all_titles if t["title_norm"] not in processed_titles]
    max_titles = api_cfg.get("max_titles_per_run", 300)
    if incremental:
        max_titles = min(max_titles, 20)
    to_fetch = to_fetch[:max_titles]

    client = PoliteHttpClient(
        source_name="openlibrary_api",
        user_agent=cfg["project"]["user_agent"],
        rate_limit_sec=api_cfg["rate_limit_sec"],
        jitter_sec=api_cfg["jitter_sec"],
        timeout_sec=api_cfg["timeout_sec"],
        max_retries=api_cfg["max_retries"],
        cb_threshold=api_cfg["circuit_breaker_threshold"],
        etags_path=etags_path,
        logger=logger,
    )

    collected_count = 0
    with api_raw_path.open("a", encoding="utf-8") as out_f:
        for item in to_fetch:
            params = {
                "title": item["title"],
                "limit": 1,
                "fields": "key,title,author_name,first_publish_year,edition_count,isbn,language",
            }
            try:
                resp, is_304 = client.get(
                    api_cfg["base_url"], params=params, use_conditional=True
                )
            except CircuitBreakerOpenError:
                client.save_etags()
                watermark["ol_processed_titles"] = sorted(processed_titles)
                save_watermark(watermark_path, watermark)
                raise

            if is_304:
                processed_titles.add(item["title_norm"])
                continue

            if not resp or resp.status_code != 200:
                continue

            try:
                data = resp.json()
            except ValueError:
                continue

            docs = data.get("docs", [])
            top_doc = docs[0] if docs else None

            record = {
                "run_id": run_id,
                "source": "openlibrary.org",
                "source_url": resp.url,
                "collected_at": utc_now_iso(),
                "parser_version": cfg["project"]["parser_version"],
                "query_title": item["title"],
                "title_norm": item["title_norm"],
                "num_found": data.get("numFound", 0),
                "payload": top_doc,
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            collected_count += 1
            processed_titles.add(item["title_norm"])

            if collected_count % 25 == 0:
                watermark["ol_processed_titles"] = sorted(processed_titles)
                save_watermark(watermark_path, watermark)

    watermark["ol_processed_titles"] = sorted(processed_titles)
    watermark["last_api_run_at"] = utc_now_iso()
    save_watermark(watermark_path, watermark)
    client.save_etags()

    return {
        "collected_records": collected_count,
        "http_metrics": client.get_metrics(),
    }
