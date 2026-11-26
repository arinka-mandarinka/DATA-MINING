from typing import Optional
from pydantic import BaseModel, Field, field_validator


class BookRecord(BaseModel):
    book_key: str = Field(..., min_length=4, max_length=256)
    title: str = Field(..., min_length=1, max_length=500)
    title_norm: str = Field(..., min_length=1, max_length=500)
    category: str = Field(..., min_length=1, max_length=120)
    price_gbp: float = Field(..., ge=0.0, le=500.0)
    rating: int = Field(..., ge=1, le=5)
    in_stock: int = Field(..., ge=0, le=1)
    availability_text: str = Field(..., min_length=1)
    product_url: str = Field(..., min_length=10)
    source: str = Field(..., min_length=1)
    collected_at: str = Field(..., min_length=10)
    updated_at: str = Field(..., min_length=10)
    parser_version: str = Field(..., min_length=1)

    @field_validator("product_url")
    @classmethod
    def validate_product_url(cls, v: str) -> str:
        if not v.startswith("https://books.toscrape.com/"):
            raise ValueError("product_url must belong to books.toscrape.com")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        cleaned = v.strip()
        if cleaned.lower() in {"", "none", "null", "n/a"}:
            raise ValueError("category cannot be empty or placeholder")
        return cleaned


class OpenLibraryRecord(BaseModel):
    title_norm: str = Field(..., min_length=1, max_length=500)
    ol_work_key: Optional[str] = None
    ol_title: Optional[str] = None
    author_name: Optional[str] = None
    first_publish_year: Optional[int] = Field(default=None, ge=1400, le=2030)
    edition_count: Optional[int] = Field(default=None, ge=0, le=50000)
    isbn: Optional[str] = None
    language: Optional[str] = None
    match_score: float = Field(..., ge=0.0, le=1.0)
    source: str = Field(..., min_length=1)
    collected_at: str = Field(..., min_length=10)
    updated_at: str = Field(..., min_length=10)
    parser_version: str = Field(..., min_length=1)

    @field_validator("ol_work_key")
    @classmethod
    def validate_work_key(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("/works/"):
            raise ValueError("ol_work_key must start with /works/")
        return v
