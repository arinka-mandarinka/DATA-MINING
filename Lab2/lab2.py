"""Сбор данных с учебного сайта https://books.toscrape.com/ через BeautifulSoup.

Поля: title (название), category (категория), price, rating, stock (числовые),
      link (ссылка), book_id (идентификатор).

"""
import os
import re
import textwrap
import time
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

START_URL = "https://books.toscrape.com/"
PAGES = 1
TIMEOUT = 10
RETRIES = 3
OCTOPARSE_CSV = "books_octoparse.csv"

OCTOPARSE_SECONDS = 15
MINUTES_CODE = 60
MINUTES_OCTOPARSE = 50
HEADERS = {"User-Agent": "Mozilla/5.0 (study scraper)"}
RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}

# Правила поиска (CSS-селекторы)
SELECTORS = [
    ("Карточка товара",   "article.product_pod",                 "страница каталога"),
    ("Название",          "h3 > a, атрибут title",               "страница каталога"),
    ("Ссылка",            "h3 > a, атрибут href (+ urljoin)",    "страница каталога"),
    ("Цена",              "p.price_color, текст",                "страница каталога"),
    ("Рейтинг",           "p.star-rating, второй класс (Three)", "страница каталога"),
    ("Следующая стр.",    "li.next > a, атрибут href",           "страница каталога"),
    ("Категория",         "ul.breadcrumb li:nth-of-type(3) a",   "страница книги"),
    ("Количество",        "p.instock.availability, число в тексте", "страница книги"),
    ("UPC",               "table th:-soup-contains('UPC') + td", "страница книги"),
    ("ID",                "число из ссылки (_1000)", "—"),
]


def get_html(url):
    """Загрузка страницы с таймаутом и повторными попытками."""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp.text
        except requests.RequestException as err:
            print(f"  попытка {attempt}/{RETRIES} не удалась: {err}")
            if attempt == RETRIES:
                raise                             # все попытки исчерпаны
            time.sleep(2 * attempt)               # пауза растёт: 2 с, 4 с


def clean(text):
    """Убрать лишние пробелы и переносы строк."""
    return " ".join(text.split()) if text else None


def parse_book_page(url):
    """Со страницы книги: категория, количество в наличии, UPC."""
    soup = BeautifulSoup(get_html(url), "html.parser")
    crumbs = soup.select("ul.breadcrumb li a")    # Home > Books > Категория
    category = clean(crumbs[2].get_text()) if len(crumbs) > 2 else None
    stock_tag = soup.select_one("p.instock.availability")
    stock = re.search(r"\d+", stock_tag.get_text()) if stock_tag else None
    upc_tag = soup.select_one("table th:-soup-contains('UPC') + td")
    return {
        "category": category,
        "stock": stock.group() if stock else None,
        "upc": clean(upc_tag.get_text()) if upc_tag else None,
    }


def scrape(start_url, pages):
    """Обход страниц каталога и сбор записей."""
    records, url = [], start_url
    for page in range(pages):
        print(f"Страница каталога {page + 1}: {url}")
        soup = BeautifulSoup(get_html(url), "html.parser")
        for card in soup.select("article.product_pod"):
            a = card.select_one("h3 > a")
            price = card.select_one("p.price_color")
            rating = card.select_one("p.star-rating")
            link = urljoin(url, a["href"]) if a else None

            # идентификатор: сначала data-*, если нет — число из ссылки
            data_attrs = {k: v for k, v in card.attrs.items() if k.startswith("data-")}
            id_match = re.search(r"_(\d+)/index\.html", link or "")
            book_id = next(iter(data_attrs.values()), None) or (id_match.group(1) if id_match else None)

            record = {
                "book_id": book_id,
                "title": clean(a.get("title")) if a else None,
                "price": clean(price.get_text()) if price else None,
                "rating": next((c for c in rating.get("class", []) if c in RATINGS), None) if rating else None,
                "link": link,
            }
            record.update(parse_book_page(link) if link else {})   # добавляем поля со страницы книги
            records.append(record)
            time.sleep(0.2)

        next_link = soup.select_one("li.next > a")
        if not next_link or page == pages - 1:
            break
        url = urljoin(url, next_link["href"])
    return records


def to_dataframe(records):
    """Очистка: числовые типы, удаление дубликатов."""
    df = pd.DataFrame(records)
    df["price"] = pd.to_numeric(df["price"].str.replace(r"[^\d.]", "", regex=True), errors="coerce")
    df["rating"] = df["rating"].map(RATINGS).astype("Int64")
    df["stock"] = pd.to_numeric(df["stock"], errors="coerce").astype("Int64")
    df["book_id"] = pd.to_numeric(df["book_id"], errors="coerce").astype("Int64")
    df = df.drop_duplicates(subset=["link"]).reset_index(drop=True)
    return df[["book_id", "title", "category", "price", "rating", "stock", "upc", "link"]]


def print_table(frame, widths):
    headers = list(frame.columns)
    line = lambda l, m, r: l + m.join("─" * (w + 2) for w in widths) + r

    def print_row(cells):
        wrapped = [textwrap.wrap(str(c), w) or [""] for c, w in zip(cells, widths)]
        for i in range(max(len(x) for x in wrapped)):
            print("│" + "│".join(f" {(x[i] if i < len(x) else ''):<{w}} " for x, w in zip(wrapped, widths)) + "│")

    print(line("┌", "┬", "┐"))
    print_row(headers)
    print(line("├", "┼", "┤"))
    for row in frame.itertuples(index=False):
        print_row(row)
    print(line("└", "┴", "┘"))


def find_column(columns, keywords):
    """Найти столбец Octoparse по ключевым словам в названии."""
    return next((c for c in columns if any(k in c.lower() for k in keywords)), None)


def missing_share(frame):
    """Доля пропусков: NaN или пустая строка после удаления пробелов."""
    empty = frame.isna() | frame.astype(str).apply(lambda s: s.str.strip() == "")
    return empty.mean().mean() * 100


def dirty_cells(frame):
    """Число текстовых ячеек с лишними пробелами или переносами строк."""
    text = frame.select_dtypes(exclude="number").astype(str)
    return int(text.apply(lambda s: s != s.str.split().str.join(" ")).sum().sum())


def compare_with_octoparse(df, seconds_code):
    """Часть B: сравнительная таблица «код vs Octoparse»."""
    seconds_code = max(seconds_code, 0.01)
    if not os.path.exists(OCTOPARSE_CSV):
        print(f"\nФайл {OCTOPARSE_CSV} не найден — сравнение с Octoparse пропущено.")
        return
    octo = pd.read_csv(OCTOPARSE_CSV, encoding="utf-8-sig")

    # сопоставление полей кода и столбцов Octoparse
    mapping = {
        "link": find_column(octo.columns, ["url", "link"]),
        "title": next((c for c in octo.columns if c.lower() in ("title", "name", "название")), None),
        "category": find_column(octo.columns, ["categ", "категор"]),
        "price": find_column(octo.columns, ["price", "цен"]),
        "rating": find_column(octo.columns, ["rating", "рейтинг"]),
        "stock": next((c for c in octo.columns if c.lower() == "stock"), None),
    }
    common = [f for f, c in mapping.items() if c]

    # совпадение записей и значений (по ссылке)
    merged = df.merge(octo, left_on="link", right_on=mapping["link"], how="inner") if mapping["link"] else df.iloc[0:0]
    def values_match(field, convert):
        col = mapping[field]
        if not col or merged.empty:
            return "—"
        return f"{(merged[field] == merged[col].map(convert)).sum()} из {len(merged)}"

    to_price = lambda v: float(re.sub(r"[^\d.]", "", str(v)) or "nan")
    to_rating = lambda v: next((RATINGS[w] for w in str(v).split() if w in RATINGS), None)
    to_text = lambda v: clean(str(v))

    rows = [
        ("Время сбора, с",               f"{seconds_code:.1f}",                  OCTOPARSE_SECONDS),
        ("Скорость, записей/мин",         f"{len(df) / seconds_code * 60:.0f}",   f"{len(octo) / OCTOPARSE_SECONDS * 60:.0f}"),
        ("Трудозатраты на создание, мин", MINUTES_CODE,                           MINUTES_OCTOPARSE),
        ("Число записей",                 len(df),                                len(octo)),
        ("Число полей",                   len(df.columns),                        len(octo.columns)),
        ("Общие поля",                    ", ".join(common),                      ", ".join(mapping[f] for f in common)),
        ("Только в этом наборе",          ", ".join(c for c in df.columns if c not in common) or "—",
                                          ", ".join(c for c in octo.columns if c not in mapping.values()) or "—"),
        ("Доля пропусков, %",             f"{missing_share(df):.1f}",             f"{missing_share(octo):.1f}"),
        ("Дубликатов",                    int(df.duplicated().sum()),             int(octo.duplicated().sum())),
        ("Числовых столбцов",             len(df.select_dtypes("number").columns), len(octo.select_dtypes("number").columns)),
        ("Ячеек с лишними пробелами",     dirty_cells(df),                        dirty_cells(octo)),
        ("Совпало записей по ссылке",     f"{len(merged)} из {len(df)}",          f"{len(merged)} из {len(octo)}"),
        ("Совпала категория",             values_match("category", to_text),      values_match("category", to_text)),
        ("Совпала цена",                  values_match("price", to_price),        values_match("price", to_price)),
        ("Совпал рейтинг",                values_match("rating", to_rating),      values_match("rating", to_rating)),
    ]
    table = pd.DataFrame(rows, columns=["Критерий", "Код (BeautifulSoup)", "Octoparse"])

    print("\n=== Сравнительная таблица «код vs Octoparse» ===")
    print_table(table, widths=[30, 26, 26])
    table.to_csv("comparison.csv", index=False, encoding="utf-8-sig")

    faster = "код" if seconds_code < OCTOPARSE_SECONDS else "Octoparse"
    ratio = max(seconds_code, OCTOPARSE_SECONDS) / min(seconds_code, OCTOPARSE_SECONDS)
    print(f"\nПо времени сбора быстрее {faster} — в {ratio:.1f} раза.")
    print("Таблица сохранена в comparison.csv")


def main():
    print("=== Правила поиска (CSS-селекторы) ===")
    print_table(pd.DataFrame(SELECTORS, columns=["Поле", "Селектор", "Где"]), widths=[16, 40, 18])
    print()

    t0 = time.perf_counter()
    df = to_dataframe(scrape(START_URL, PAGES))
    elapsed = time.perf_counter() - t0

    df.to_csv("books.csv", index=False, encoding="utf-8-sig")

    print(f"\nСобрано за {elapsed:.1f} с, сохранено в books.csv")
    print(f"Число строк: {len(df)}")
    print(f"Общая доля пропусков: {df.isna().mean().mean() * 100:.1f} %")

    print("\nПропуски и типы данных по столбцам:")
    info = pd.DataFrame({
        "Столбец": df.columns,
        "Пропуски, %": (df.isna().mean() * 100).round(1).values,
        "Тип": df.dtypes.astype(str).values,
    })
    print_table(info, widths=[10, 12, 10])

    print("\nПервые пять записей (ссылка сокращена, полная — в books.csv):")
    preview = df.head(5).copy()
    preview["price"] = preview["price"].map("{:.2f}".format)
    preview["link"] = preview["link"].str.extract(r"catalogue/([^/]+)/", expand=False)
    print_table(preview, widths=[7, 22, 18, 6, 6, 5, 16, 24])

    compare_with_octoparse(df, elapsed)


if __name__ == "__main__":
    main()