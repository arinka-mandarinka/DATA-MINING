"""Схема записи данных о книге (Вариант 2: books.toscrape.com по категориям)."""

import scrapy


class BookItem(scrapy.Item):
    """Запись карточки книги с полями уровня 1 (список) и уровня 2 (карточка)."""

    # Бизнес-ключ
    upc = scrapy.Field()

    # Основные атрибуты карточки
    title = scrapy.Field()
    category = scrapy.Field()           # Категория из хлебных крошек карточки
    category_from_menu = scrapy.Field() # Категория из уровня 1 (передана через cb_kwargs)
    description = scrapy.Field()

    # Сырые поля (очищаются и удаляются в CleanPipeline)
    price_raw = scrapy.Field()
    price_excl_tax_raw = scrapy.Field()
    price_incl_tax_raw = scrapy.Field()
    tax_raw = scrapy.Field()
    list_price_raw = scrapy.Field()
    availability_raw = scrapy.Field()
    rating_raw = scrapy.Field()
    reviews_raw = scrapy.Field()

    # Очищенные типизированные поля
    price = scrapy.Field()              # float (основная цена на карточке)
    price_excl_tax = scrapy.Field()     # float (цена без налога, обязательное поле варианта 2)
    price_incl_tax = scrapy.Field()     # float (цена с налогом)
    tax = scrapy.Field()                # float (налог)
    stock = scrapy.Field()              # int (количество в наличии)
    rating = scrapy.Field()             # int (1..5)
    reviews = scrapy.Field()            # int (число отзывов)

    # Служебные поля и трассировка
    url = scrapy.Field()
    list_page = scrapy.Field()
    scraped_at = scrapy.Field()         # ISO-8601 UTC timestamp
