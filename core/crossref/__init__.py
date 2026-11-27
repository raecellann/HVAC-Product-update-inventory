"""Cross-reference enrichment: find UPC/GTIN values for Shopify parts.

Two-stage flow:
  1. Crawler reads a CSV of (product_id, sku, title) rows, queries each source
     by SKU, and writes one results CSV plus a SQLite cache for resume/dedup.
  2. A separate push script reads the results CSV and sets Shopify's
     `custom.cross_reference` metafield per product.

Stage 1 never writes to Shopify. Stage 2 is the only step that does.
"""
from .sources import Source, SourceResult, TemplateSource, gs1_valid
from .crawler import Crawler, CrawlResult, CrawlRow

__all__ = [
    "TemplateSource",
    "Source",
    "SourceResult",
    "Crawler",
    "CrawlResult",
    "CrawlRow",
    "gs1_valid",
]
