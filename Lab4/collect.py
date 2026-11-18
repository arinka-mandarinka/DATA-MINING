#!/usr/bin/env python
"""Лаб. 4, вариант 2: браузерный сбор quotes.toscrape.com/js/ и /js-delayed/ (sync API).

Коды возврата: 0 — успех; 1 — непредвиденная ошибка; 2 — собрано меньше --min-items;
3 — smoke-check не пройден; 4 — были таймауты страниц (только с --strict).
"""
import argparse, csv, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, expect, TimeoutError as PWTimeout

BASE = "https://quotes.toscrape.com"
SOURCES = {"js": f"{BASE}/js/", "js-delayed": f"{BASE}/js-delayed/"}
UA = "course-crawler/1.0 (educational use; lab4 variant 2)"
ITEM_TIMEOUT = {"js": 10_000, "js-delayed": 25_000}
EXTRACT_JS = """els => els.map((el, i) => ({position: i + 1,
    text: el.querySelector('.text')?.innerText?.trim() ?? null,
    author: el.querySelector('.author')?.innerText?.trim() ?? null,
    tags: Array.from(el.querySelectorAll('.tag')).map(t => t.innerText.trim()).join('; ')}))"""

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def page_number(url):
    m = re.search(r"/page/(\d+)/", url)
    return int(m.group(1)) if m else 1

def block(route):
    if route.request.resource_type in {"image", "media", "font"}:
        route.abort()
    else:
        route.continue_()

def ensure_auth(browser, state):
    if state.exists():
        return
    ctx = browser.new_context(user_agent=UA)
    try:
        page = ctx.new_page()
        page.goto(f"{BASE}/login")
        page.get_by_label("Username").fill("student")
        page.get_by_label("Password").fill("student")
        page.get_by_role("button", name="Login").click()
        expect(page.get_by_role("link", name="Logout")).to_be_visible()
        ctx.storage_state(path=str(state))
    finally:
        ctx.close()

def collect(ctx, source, art, max_pages, max_items, stall_limit, pause):
    page = ctx.new_page()
    recs, log, seen, stalls, timeouts = [], [], set(), 0, 0
    try:
        page.goto(SOURCES[source], wait_until="domcontentloaded")
        for it in range(1, max_pages + 1):
            t0, url, pno, status = time.perf_counter(), page.url, page_number(page.url), "ok"
            try:
                page.locator(".quote").first.wait_for(state="visible", timeout=ITEM_TIMEOUT[source])
                batch = page.locator(".quote").evaluate_all(EXTRACT_JS)
            except PWTimeout:
                status, batch, timeouts = "timeout", [], timeouts + 1
                base = art / f"{source}_p{pno}_{datetime.now():%Y%m%d_%H%M%S}"
                page.screenshot(path=f"{base}.png", full_page=True)
                Path(f"{base}.html").write_text(page.content(), encoding="utf-8")
                print(f"[WARN] {source} стр.{pno}: таймаут, артефакты {base.name}.*", file=sys.stderr)
            new = 0
            for r in batch:
                if r["text"] and (source, r["text"]) in seen:
                    continue
                seen.add((source, r["text"]))
                r.update(source=source, page=pno, page_url=url, collected_at=now_iso())
                recs.append(r); new += 1
            log.append({"source": source, "iteration": it, "url": url, "elements": len(batch),
                        "increment": new, "total": len(recs),
                        "duration_s": round(time.perf_counter() - t0, 3), "timestamp": now_iso(),
                        "status": status})
            stalls = stalls + 1 if new == 0 else 0
            if stalls >= stall_limit or len(recs) >= max_items:
                break
            nxt = page.get_by_role("link", name="Next")
            if nxt.count() == 0:
                break
            time.sleep(pause)                       # пауза вежливости
            nxt.click()
            page.wait_for_url(f"**/page/{pno + 1}/", wait_until="domcontentloaded")
    finally:
        page.close()
    return recs[:max_items], log, timeouts

def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8-sig"); return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__)

    ap.add_argument(
        "--source",
        choices=["js", "js-delayed", "all"],
        default="all"
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("."),
        help="каталог для сохранения результатов"
    )
    ap.add_argument(
        "--max-items",
        type=int,
        default=100,
        help="лимит записей на источник"
    )
    ap.add_argument("--max-pages", type=int, default=15)
    ap.add_argument("--stall-limit", type=int, default=2)
    ap.add_argument(
        "--min-items",
        type=int,
        default=200,
        help="минимум записей для кода 0"
    )
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--strict", action="store_true")

    a = ap.parse_args(argv)

    out = Path(a.out_dir); art = out / "artifacts"; art.mkdir(parents=True, exist_ok=True)
    state = out / "auth_state.json"
    sources = list(SOURCES) if a.source == "all" else [a.source]
    recs, log, timeouts = [], [], 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not a.headed)
            try:
                ensure_auth(browser, state)
                ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900},
                                          locale="ru-RU", storage_state=str(state))
                ctx.set_default_timeout(15_000)
                ctx.set_default_navigation_timeout(30_000)
                ctx.route("**/*", block)
                try:
                    for s in sources:
                        r, l, t = collect(ctx, s, art, a.max_pages, a.max_items, a.stall_limit, a.pause)
                        recs += r; log += l; timeouts += t
                finally:
                    ctx.close()
            finally:
                browser.close()
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    write_csv(out / "data_browser.csv", recs)
    write_csv(out / "collection_log.csv", log)
    print(f"[INFO] записей: {len(recs)}, итераций: {len(log)}, таймаутов: {timeouts} → {out}/")

    for field in ("text", "author"):
        share = sum(1 for r in recs if not (r.get(field) or "").strip()) / max(len(recs), 1)
        if share > 0.05:
            print(f"[ERROR] smoke-check: пустых «{field}» {share:.1%} > 5%", file=sys.stderr)
            return 3
    if len(recs) < a.min_items:
        print(f"[ERROR] собрано {len(recs)} < --min-items {a.min_items}", file=sys.stderr)
        return 2
    if a.strict and timeouts:
        return 4
    return 0

if __name__ == "__main__":
    sys.exit(main())
