-- Схема хранилища данных: Вариант 2 «Книги и авторы» (SQLite)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS authors (
    author_key     TEXT PRIMARY KEY,
    author_name    TEXT NOT NULL CHECK (length(trim(author_name)) > 0),
    birth_year     INTEGER CHECK (birth_year IS NULL OR (birth_year BETWEEN 1000 AND 2026)),
    top_work       TEXT,
    work_count     INTEGER CHECK (work_count IS NULL OR work_count >= 0),
    source         TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    collected_at   TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS books (
    book_key       TEXT PRIMARY KEY,
    author_key     TEXT NOT NULL REFERENCES authors(author_key) ON UPDATE CASCADE ON DELETE RESTRICT,
    title          TEXT NOT NULL CHECK (length(trim(title)) > 0),
    category       TEXT,
    price          TEXT CHECK (price IS NULL OR CAST(price AS REAL) > 0),
    stock          INTEGER CHECK (stock IS NULL OR stock >= 0),
    rating         INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    in_stock       INTEGER CHECK (in_stock IS NULL OR in_stock IN (0, 1)),
    publish_year   INTEGER CHECK (publish_year IS NULL OR publish_year BETWEEN 1450 AND 2026),
    isbn           TEXT,
    source         TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    collected_at   TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_books_author_key ON books(author_key);
CREATE INDEX IF NOT EXISTS idx_books_category   ON books(category);
CREATE INDEX IF NOT EXISTS idx_books_source     ON books(source);
CREATE INDEX IF NOT EXISTS idx_books_rating     ON books(rating);
CREATE INDEX IF NOT EXISTS idx_authors_name     ON authors(author_name);

-- История изменений (снапшоты по дате/прогону) для отслеживания динамики цен, остатков и рейтингов
CREATE TABLE IF NOT EXISTS book_history (
    book_key    TEXT NOT NULL REFERENCES books(book_key) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    price       TEXT,
    stock       INTEGER,
    rating      INTEGER,
    PRIMARY KEY (book_key, observed_at)
);

-- Таблица отбраковки: хранит отклоненные записи с указанием поля и причины
CREATE TABLE IF NOT EXISTS rejects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    book_key   TEXT,
    source     TEXT,
    field_name TEXT,
    reason     TEXT NOT NULL,
    payload    TEXT NOT NULL,
    seen_at    TEXT NOT NULL
);
