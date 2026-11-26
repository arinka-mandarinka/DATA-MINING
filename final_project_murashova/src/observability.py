import json
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CircuitBreakerOpenError(Exception):
    """Исключение при срабатывании предохранителя."""


class StructuredLogger:
    def __init__(self, log_path: Path, run_id: str):
        self.log_path = log_path
        self.run_id = run_id
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, level: str = "INFO", **kwargs: Any) -> None:
        record = {
            "timestamp": utc_now_iso(),
            "run_id": self.run_id,
            "level": level,
            "event": event,
            **kwargs,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


class PoliteHttpClient:
    """HTTP-клиент с rate limiting, джиттером, Retry-After, ETag/Last-Modified и Circuit Breaker."""

    TRANSIENT_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        source_name: str,
        user_agent: str,
        rate_limit_sec: float,
        jitter_sec: float,
        timeout_sec: int,
        max_retries: int,
        cb_threshold: int,
        etags_path: Path,
        logger: StructuredLogger,
    ):
        self.source_name = source_name
        self.rate_limit_sec = rate_limit_sec
        self.jitter_sec = jitter_sec
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.cb_threshold = cb_threshold
        self.etags_path = etags_path
        self.logger = logger

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
        )

        self.last_request_ts = 0.0
        self.consecutive_failures = 0
        self.etags: Dict[str, Dict[str, str]] = self._load_etags()

        self.requests_total = 0
        self.status_counts: Dict[str, int] = {}
        self.retries_total = 0
        self.errors_total = 0
        self.latencies_ms: List[float] = []
        self.bytes_received = 0

    def _load_etags(self) -> Dict[str, Dict[str, str]]:
        if self.etags_path.exists():
            try:
                return json.loads(self.etags_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def save_etags(self) -> None:
        self.etags_path.parent.mkdir(parents=True, exist_ok=True)
        self.etags_path.write_text(
            json.dumps(self.etags, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _sleep_politely(self) -> None:
        elapsed = time.monotonic() - self.last_request_ts
        delay = self.rate_limit_sec + random.uniform(0, self.jitter_sec)
        if elapsed < delay:
            time.sleep(delay - elapsed)

    def get(
        self, url: str, params: Optional[Dict[str, Any]] = None, use_conditional: bool = True
    ) -> Tuple[Optional[requests.Response], bool]:
        if self.consecutive_failures >= self.cb_threshold:
            self.logger.log(
                "circuit_breaker_open",
                level="ERROR",
                source=self.source_name,
                url=url,
                consecutive_failures=self.consecutive_failures,
            )
            raise CircuitBreakerOpenError(
                f"Circuit breaker open for {self.source_name} after {self.consecutive_failures} failures"
            )

        cache_key = url if not params else f"{url}?{json.dumps(params, sort_keys=True)}"
        headers: Dict[str, str] = {}
        if use_conditional and cache_key in self.etags:
            if "etag" in self.etags[cache_key]:
                headers["If-None-Match"] = self.etags[cache_key]["etag"]
            if "last_modified" in self.etags[cache_key]:
                headers["If-Modified-Since"] = self.etags[cache_key]["last_modified"]

        for attempt in range(self.max_retries + 1):
            self._sleep_politely()
            self.last_request_ts = time.monotonic()
            start = time.perf_counter()
            self.requests_total += 1

            try:
                resp = self.session.get(
                    url, params=params, headers=headers, timeout=self.timeout_sec
                )
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                self.latencies_ms.append(latency_ms)
                code_str = str(resp.status_code)
                self.status_counts[code_str] = self.status_counts.get(code_str, 0) + 1
                self.bytes_received += len(resp.content)

                self.logger.log(
                    "http_request",
                    source=self.source_name,
                    url=resp.url,
                    status_code=resp.status_code,
                    latency_ms=latency_ms,
                    bytes=len(resp.content),
                    attempt=attempt + 1,
                )

                if resp.status_code == 304:
                    self.consecutive_failures = 0
                    return resp, True

                if resp.status_code == 200:
                    self.consecutive_failures = 0
                    meta = {}
                    if resp.headers.get("ETag"):
                        meta["etag"] = resp.headers["ETag"]
                    if resp.headers.get("Last-Modified"):
                        meta["last_modified"] = resp.headers["Last-Modified"]
                    if meta:
                        self.etags[cache_key] = meta
                    return resp, False

                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    self.errors_total += 1
                    self.consecutive_failures += 1
                    return resp, False

                if resp.status_code in self.TRANSIENT_STATUSES:
                    self.errors_total += 1
                    if attempt < self.max_retries:
                        self.retries_total += 1
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit():
                            wait_sec = float(retry_after)
                        else:
                            wait_sec = (2 ** attempt) + random.uniform(0.1, 0.5)
                        self.logger.log(
                            "http_retry",
                            level="WARNING",
                            source=self.source_name,
                            url=url,
                            status_code=resp.status_code,
                            wait_sec=round(wait_sec, 2),
                            attempt=attempt + 1,
                        )
                        time.sleep(wait_sec)
                        continue
                    self.consecutive_failures += 1
                    return resp, False

            except requests.RequestException as exc:
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                self.latencies_ms.append(latency_ms)
                self.errors_total += 1
                self.status_counts["network_error"] = (
                    self.status_counts.get("network_error", 0) + 1
                )
                self.logger.log(
                    "http_exception",
                    level="WARNING",
                    source=self.source_name,
                    url=url,
                    error=str(exc),
                    attempt=attempt + 1,
                )
                if attempt < self.max_retries:
                    self.retries_total += 1
                    time.sleep((2 ** attempt) + random.uniform(0.1, 0.5))
                    continue
                self.consecutive_failures += 1
                return None, False

        return None, False

    def get_metrics(self) -> Dict[str, Any]:
        sorted_lat = sorted(self.latencies_ms)
        median_ms = round(statistics.median(sorted_lat), 2) if sorted_lat else 0.0
        p95_idx = int(len(sorted_lat) * 0.95) - 1 if sorted_lat else 0
        p95_ms = round(sorted_lat[max(0, p95_idx)], 2) if sorted_lat else 0.0
        error_rate = (
            round(self.errors_total / self.requests_total, 4)
            if self.requests_total > 0
            else 0.0
        )
        return {
            "source": self.source_name,
            "requests_total": self.requests_total,
            "status_counts": self.status_counts,
            "errors_total": self.errors_total,
            "error_rate": error_rate,
            "retries_total": self.retries_total,
            "latency_median_ms": median_ms,
            "latency_p95_ms": p95_ms,
            "bytes_received": self.bytes_received,
        }


def evaluate_alerts(
    run_report: Dict[str, Any],
    alert_cfg: Dict[str, float],
    reports_dir: Path,
) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []

    if run_report["http_summary"]["error_rate"] > alert_cfg["max_error_rate"]:
        alerts.append(
            {
                "rule": "high_http_error_rate",
                "value": run_report["http_summary"]["error_rate"],
                "threshold": alert_cfg["max_error_rate"],
            }
        )

    if run_report["http_summary"]["latency_p95_ms"] > alert_cfg["max_p95_latency_ms"]:
        alerts.append(
            {
                "rule": "high_p95_latency_ms",
                "value": run_report["http_summary"]["latency_p95_ms"],
                "threshold": alert_cfg["max_p95_latency_ms"],
            }
        )

    total_processed = run_report["records"]["clean"] + run_report["records"]["rejected"]
    reject_rate = (
        run_report["records"]["rejected"] / total_processed if total_processed > 0 else 0.0
    )
    if reject_rate > alert_cfg["max_reject_rate"]:
        alerts.append(
            {
                "rule": "high_reject_rate",
                "value": round(reject_rate, 4),
                "threshold": alert_cfg["max_reject_rate"],
            }
        )

    prev_reports = sorted(reports_dir.glob("run_report_*.json"))
    if prev_reports:
        try:
            prev = json.loads(prev_reports[-1].read_text(encoding="utf-8"))
            prev_clean = prev.get("records", {}).get("clean", 0)
            curr_clean = run_report["records"]["clean"]
            if prev_clean > 0:
                drop_pct = (prev_clean - curr_clean) / prev_clean
                if drop_pct > alert_cfg["max_volume_drop_pct"]:
                    alerts.append(
                        {
                            "rule": "volume_drop_vs_previous_run",
                            "value": round(drop_pct, 4),
                            "threshold": alert_cfg["max_volume_drop_pct"],
                            "previous_clean": prev_clean,
                            "current_clean": curr_clean,
                        }
                    )
        except Exception:
            pass

    return alerts
