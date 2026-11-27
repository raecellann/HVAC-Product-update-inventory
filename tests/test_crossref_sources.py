"""Unit tests for the cross-reference source adapters."""
from __future__ import annotations

import pytest


def test_template_substitutes_case_placeholders():
    from core.crossref.sources import TemplateSource
    src = TemplateSource("https://www.alltemp.ca/product/{sku_lower}/")
    assert src._url("WG840596") == "https://www.alltemp.ca/product/wg840596/"

    src_upper = TemplateSource("https://x.example/p/{sku_upper}.html")
    assert src_upper._url("wg840596") == "https://x.example/p/WG840596.html"

    src_asis = TemplateSource("https://x.example/p/{sku}")
    assert src_asis._url("WG-840596") == "https://x.example/p/WG-840596"


def test_template_name_defaults_to_hostname():
    from core.crossref.sources import TemplateSource
    src = TemplateSource("https://www.alltemp.ca/product/{sku_lower}/")
    assert src.name == "www.alltemp.ca"


def test_template_rejects_missing_placeholder():
    from core.crossref.sources import TemplateSource
    with pytest.raises(ValueError):
        TemplateSource("https://www.alltemp.ca/product/wg840596/")


def test_gs1_valid():
    from core.crossref.sources import gs1_valid
    # Real GTIN-14 from the alltemp example (DiversiTech WG840596).
    assert gs1_valid("00686109004639") is True
    # Wrong length.
    assert gs1_valid("12345") is False
    # Right length, wrong checksum.
    assert gs1_valid("12345678901234") is False
    # Non-numeric.
    assert gs1_valid("abcdefghijkl") is False


def test_extract_finds_gtin_near_any_label():
    from core.crossref.sources import TemplateSource, DEFAULT_LABELS
    html = """
    <html><body>
      <div>Weight 20.80 lbs</div>
      <div>Cross Reference 00686109004639</div>
      <div>unrelated digits 1234567890</div>
    </body></html>
    """
    assert TemplateSource._extract(html, DEFAULT_LABELS, 400) == ["00686109004639"]


def test_extract_matches_upc_label_too():
    from core.crossref.sources import TemplateSource, DEFAULT_LABELS
    html = "<html><body>UPC: 00686109004639 and some other text</body></html>"
    assert TemplateSource._extract(html, DEFAULT_LABELS, 400) == ["00686109004639"]


def test_extract_skips_when_no_label_matches():
    from core.crossref.sources import TemplateSource, DEFAULT_LABELS
    html = "<html><body>no label here, just 00686109004639</body></html>"
    assert TemplateSource._extract(html, DEFAULT_LABELS, 400) == []


def test_extract_rejects_bad_checksum():
    from core.crossref.sources import TemplateSource, DEFAULT_LABELS
    html = "<html><body>Cross Reference 12345678901234</body></html>"
    assert TemplateSource._extract(html, DEFAULT_LABELS, 400) == []
