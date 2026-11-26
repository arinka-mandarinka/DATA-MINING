import json
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.observability import (
    CircuitBreakerOpenError,
    PoliteHttpClient,
    StructuredLogger,
    utc_now_iso,
)


def load_watermark(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_watermark(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_books_html(
    cfg: Dict[str, Any],
    run_id: str,
    logger: StructuredLogger,
    incremental: bool = False,
    simulate_failure: bool = False,
) -> Dict[str, Any]:
    src_cfg = cfg["sources"]["books_html"]
    raw_path = Path(cfg["paths"]["raw_dir"]) / "books_raw.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    etags_path = Path(cfg["paths"]["state_dir"]) / "etags.json"
    watermark_path = Path(cfg["paths"]["state_dir"]) / "watermark.json"
    watermark = load_watermark(watermark_path)

    client = PoliteHttpClient(
        source_name="books_toscrape_html",
        user_agent=cfg["project"]["user_agent"],
        rate_limit_sec=src_cfg["rate_limit_sec"],
        jitter_sec=src_cfg["jitter_sec"],
        timeout_sec=src_cfg["timeout_sec"],
        max_retries=src_cfg["max_retries"],
        cb_threshold=src_cfg["circuit_breaker_threshold"],
        etags_path=etags_path,
        logger=logger,
    )

    if simulate_failure:
        logger.log("simulate_failure_triggered", level="WARNING", target="httpbin_503")
        for _ in range(src_cfg["circuit_breaker_threshold"] + 1):
            client.get("https://httpbin.org/status/503", use_conditional=False)

    base_url = src_cfg["base_url"]
    resp, not_mod = client.get(base_url, use_conditional=incremental)
    categories_map: Dict[str, str] = {}

    if resp and resp.status_code == 200:
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("ul.nav-list ul li a"):
            cat_name = a.get_text(strip=True)
            cat_href = urljoin(base_url, a.get("href", ""))
            categories_map[cat_name] = cat_href

    completed_categories = set(watermark.get("completed_categories", []))
    collected_count = 0
    skipped_304 = 0

    if not categories_map and not_mod:
        logger.log("html_index_not_modified_304", source="books_toscrape_html")
        skipped_304 += 1

    if not categories_map and "categories_map" in watermark:
        categories_map = watermark["categories_map"]
    elif categories_map:
        watermark["categories_map"] = categories_map
        save_watermark(watermark_path, watermark)

    with raw_path.open("a", encoding="utf-8") as out_f:
        for cat_name, cat_url in categories_map.items():
            if not incremental and cat_name in completed_categories:
                continue

            next_url = cat_url
            while next_url:
                try:
                    page_resp, is_304 = client.get(next_url, use_conditional=True)
                except CircuitBreakerOpenError:
                    client.save_etags()
                    save_watermark(watermark_path, watermark)
                    raise

                if is_304:
                    skipped_304 += 1
                    break

                if not page_resp or page_resp.status_code != 200:
                    break

                page_resp.encoding = "utf-8"
                page_soup = BeautifulSoup(page_resp.text, "html.parser")
                articles = page_soup.select("article.product_pod")

                for art in articles:
                    a_tag = art.select_one("h3 a")
                    title = a_tag.get("title", "").strip() if a_tag else ""
                    rel_url = a_tag.get("href", "") if a_tag else ""
                    product_url = urljoin(next_url, rel_url)

                    price_el = art.select_one("p.price_color")
                    price_raw = price_el.get_text(strip=True) if price_el else ""

                    avail_el = art.select_one("p.instock.availability")
                    avail_raw = avail_el.get_text(strip=True) if avail_el else ""

                    rating_el = art.select_one("p.star-rating")
                    rating_classes = rating_el.get("class", []) if rating_el else []
                    rating_word = next(
                        (c for c in rating_classes if c != "star-rating"), ""
                    )

                    record = {
                        "run_id": run_id,
                        "source": "books.toscrape.com",
                        "source_url": next_url,
                        "collected_at": utc_now_iso(),
                        "parser_version": cfg["project"]["parser_version"],
                        "payload": {
                            "title": title,
                            "category": cat_name,
                            "price_raw": price_raw,
                            "rating_word": rating_word,
                            "availability_raw": avail_raw,
                            "product_url": product_url,
                        },
                    }
                    out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    collected_count += 1

                next_btn = page_soup.select_one("li.next a")
                next_url = urljoin(next_url, next_btn["href"]) if next_btn else None

            completed_categories.add(cat_name)
            watermark["completed_categories"] = sorted(completed_categories)
            watermark["last_html_run_at"] = utc_now_iso()
            save_watermark(watermark_path, watermark)

    watermark["completed_categories"] = []
    save_watermark(watermark_path, watermark)
    client.save_etags()

    return {
        "collected_records": collected_count,
        "skipped_304": skipped_304,
        "http_metrics": client.get_metrics(),
    }
