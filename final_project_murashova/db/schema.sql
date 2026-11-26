PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS books (
    book_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    category TEXT NOT NULL,
    price_gbp REAL NOT NULL CHECK (price_gbp >= 0 AND price_gbp <= 500),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    in_stock INTEGER NOT NULL CHECK (in_stock IN (0, 1)),
    availability_text TEXT NOT NULL,
    product_url TEXT NOT NULL,
    source TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    parser_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS openlibrary_enrichment (
    title_norm TEXT PRIMARY KEY,
    ol_work_key TEXT,
    ol_title TEXT,
    author_name TEXT,
    first_publish_year INTEGER CHECK (first_publish_year IS NULL OR (first_publish_year >= 1400 AND first_publish_year <= 2030)),
    edition_count INTEGER CHECK (edition_count IS NULL OR edition_count >= 0),
    isbn TEXT,
    language TEXT,
    match_score REAL NOT NULL CHECK (match_score >= 0.0 AND match_score <= 1.0),
    source TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    parser_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    quality_verdict TEXT NOT NULL,
    books_collected INTEGER NOT NULL,
    books_clean INTEGER NOT NULL,
    books_rejected INTEGER NOT NULL,
    api_enriched INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_books_title_norm ON books(title_norm);
CREATE INDEX IF NOT EXISTS idx_books_category ON books(category);
CREATE INDEX IF NOT EXISTS idx_books_rating ON books(rating);
CREATE INDEX IF NOT EXISTS idx_ol_publish_year ON openlibrary_enrichment(first_publish_year);
