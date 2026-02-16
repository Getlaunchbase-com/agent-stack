"""Vendor pricing tools — fetch pricing and availability from configured sources.

Provides three vendor adapters (Grainger, Graybar, HD Supply) that return
structured results with consistent schema:
    {vendor, sku, price, availability, leadTime, url, confidence}

Safeguards:
    - Per-vendor rate limiting (token bucket)
    - Configurable User-Agent header
    - Retry with exponential backoff on transient failures
    - Stable "no result" JSON outputs (never null)
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import logging
from typing import Any
from urllib.parse import quote_plus

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
USER_AGENT = os.getenv(
    "VENDOR_USER_AGENT",
    "AgentStack-PricingBot/1.0 (+https://github.com/Getlaunchbase-com/agent-stack)",
)
REQUEST_TIMEOUT = int(os.getenv("VENDOR_REQUEST_TIMEOUT", "15"))
MAX_RETRIES = int(os.getenv("VENDOR_MAX_RETRIES", "3"))
RATE_LIMIT_RPM = int(os.getenv("VENDOR_RATE_LIMIT_RPM", "30"))

WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspaces")

# ---------------------------------------------------------------------------
# Rate limiter — simple per-vendor token bucket
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Token-bucket rate limiter scoped per vendor key."""

    def __init__(self, rpm: int = 30):
        self._interval = 60.0 / max(rpm, 1)
        self._last: dict[str, float] = {}

    def wait(self, vendor: str) -> None:
        now = time.monotonic()
        last = self._last.get(vendor, 0.0)
        delta = self._interval - (now - last)
        if delta > 0:
            time.sleep(delta)
        self._last[vendor] = time.monotonic()


_limiter = _RateLimiter(rpm=RATE_LIMIT_RPM)

# ---------------------------------------------------------------------------
# HTTP session with retry / backoff
# ---------------------------------------------------------------------------

def _http_session() -> requests.Session:
    """Build a requests.Session with retry + exponential backoff."""
    s = requests.Session()
    retries = Retry(
        total=MAX_RETRIES,
        backoff_factor=0.5,           # 0.5s, 1s, 2s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": USER_AGENT})
    return s


# ---------------------------------------------------------------------------
# Stable "no result" helper
# ---------------------------------------------------------------------------

def _empty_result(vendor: str, query: str, reason: str = "no_match") -> dict:
    """Return a stable, non-null result when a vendor has no data."""
    return {
        "vendor": vendor,
        "sku": "",
        "price": None,
        "currency": "USD",
        "availability": "unknown",
        "leadTime": "",
        "url": "",
        "confidence": 0.0,
        "query": query,
        "reason": reason,
    }


def _make_result(
    vendor: str,
    query: str,
    *,
    sku: str = "",
    price: float | None = None,
    currency: str = "USD",
    availability: str = "unknown",
    lead_time: str = "",
    url: str = "",
    confidence: float = 0.0,
) -> dict:
    return {
        "vendor": vendor,
        "sku": sku,
        "price": price,
        "currency": currency,
        "availability": availability,
        "leadTime": lead_time,
        "url": url,
        "confidence": round(confidence, 2),
        "query": query,
    }


# ===================================================================
# Vendor adapters
# ===================================================================

# Each adapter follows the same contract:
#   def fetch(query: str, session: requests.Session) -> list[dict]
# Returns a list of result dicts (may be empty — caller wraps with
# _empty_result when nothing is found).

class _GraingerAdapter:
    """Grainger industrial supply — public search scrape."""

    VENDOR = "grainger"
    SEARCH_URL = "https://www.grainger.com/search?searchQuery={q}"

    @classmethod
    def fetch(cls, query: str, session: requests.Session) -> list[dict]:
        _limiter.wait(cls.VENDOR)
        url = cls.SEARCH_URL.format(q=quote_plus(query))
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("Grainger request failed: %s", exc)
            return []

        if resp.status_code != 200:
            logger.warning("Grainger returned %d", resp.status_code)
            return []

        return cls._parse(resp.text, query, url)

    @classmethod
    def _parse(cls, html: str, query: str, page_url: str) -> list[dict]:
        results: list[dict] = []

        # Extract product entries from JSON-LD or structured markup
        # Pattern: look for product data in the page
        sku_pattern = re.compile(
            r'data-product-id="([^"]+)"[^>]*'
            r'.*?data-price="([\d.]+)"',
            re.DOTALL,
        )
        # Fallback: look for common Grainger product patterns
        price_pattern = re.compile(
            r'(?:\"price\"\s*:\s*\"?([\d.]+)\"?)',
        )
        sku_id_pattern = re.compile(
            r'(?:\"sku\"\s*:\s*\"([A-Z0-9]+)\"'
            r'|item\s*#\s*:?\s*([A-Z0-9]+))',
            re.IGNORECASE,
        )
        avail_pattern = re.compile(
            r'(?:in\s*stock|available|ships?\s*\w+)',
            re.IGNORECASE,
        )

        prices = price_pattern.findall(html)
        skus = sku_id_pattern.findall(html)
        in_stock = bool(avail_pattern.search(html))

        if prices and skus:
            # Take first match as primary result
            sku_val = skus[0][0] or skus[0][1]
            price_val = float(prices[0])
            results.append(
                _make_result(
                    cls.VENDOR,
                    query,
                    sku=sku_val,
                    price=price_val,
                    availability="in_stock" if in_stock else "check_vendor",
                    lead_time="1-3 business days" if in_stock else "call_for_lead_time",
                    url=page_url,
                    confidence=0.65,
                )
            )
        elif prices:
            results.append(
                _make_result(
                    cls.VENDOR,
                    query,
                    price=float(prices[0]),
                    availability="in_stock" if in_stock else "check_vendor",
                    url=page_url,
                    confidence=0.4,
                )
            )

        return results


class _GraybarAdapter:
    """Graybar electrical supply — public search scrape."""

    VENDOR = "graybar"
    SEARCH_URL = "https://www.graybar.com/search?q={q}"

    @classmethod
    def fetch(cls, query: str, session: requests.Session) -> list[dict]:
        _limiter.wait(cls.VENDOR)
        url = cls.SEARCH_URL.format(q=quote_plus(query))
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("Graybar request failed: %s", exc)
            return []

        if resp.status_code != 200:
            logger.warning("Graybar returned %d", resp.status_code)
            return []

        return cls._parse(resp.text, query, url)

    @classmethod
    def _parse(cls, html: str, query: str, page_url: str) -> list[dict]:
        results: list[dict] = []

        price_pattern = re.compile(
            r'(?:\"price\"\s*:\s*\"?([\d.]+)\"?'
            r'|\$\s*([\d,]+\.?\d*))',
        )
        sku_pattern = re.compile(
            r'(?:\"sku\"\s*:\s*\"([A-Z0-9\-]+)\"'
            r'|catalog\s*#\s*:?\s*([A-Z0-9\-]+))',
            re.IGNORECASE,
        )
        avail_pattern = re.compile(
            r'(?:in\s*stock|available|ships?\s*\w+)',
            re.IGNORECASE,
        )

        prices = price_pattern.findall(html)
        skus = sku_pattern.findall(html)
        in_stock = bool(avail_pattern.search(html))

        if prices and skus:
            sku_val = skus[0][0] or skus[0][1]
            raw = prices[0][0] or prices[0][1]
            price_val = float(raw.replace(",", ""))
            results.append(
                _make_result(
                    cls.VENDOR,
                    query,
                    sku=sku_val,
                    price=price_val,
                    availability="in_stock" if in_stock else "check_vendor",
                    lead_time="2-5 business days" if in_stock else "call_for_lead_time",
                    url=page_url,
                    confidence=0.60,
                )
            )
        elif prices:
            raw = prices[0][0] or prices[0][1]
            results.append(
                _make_result(
                    cls.VENDOR,
                    query,
                    price=float(raw.replace(",", "")),
                    availability="in_stock" if in_stock else "check_vendor",
                    url=page_url,
                    confidence=0.35,
                )
            )

        return results


class _HDSupplyAdapter:
    """HD Supply — public search scrape."""

    VENDOR = "hdsupply"
    SEARCH_URL = "https://hdsupply.com/search?q={q}"

    @classmethod
    def fetch(cls, query: str, session: requests.Session) -> list[dict]:
        _limiter.wait(cls.VENDOR)
        url = cls.SEARCH_URL.format(q=quote_plus(query))
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("HD Supply request failed: %s", exc)
            return []

        if resp.status_code != 200:
            logger.warning("HD Supply returned %d", resp.status_code)
            return []

        return cls._parse(resp.text, query, url)

    @classmethod
    def _parse(cls, html: str, query: str, page_url: str) -> list[dict]:
        results: list[dict] = []

        price_pattern = re.compile(
            r'(?:\"price\"\s*:\s*\"?([\d.]+)\"?'
            r'|\$\s*([\d,]+\.?\d*))',
        )
        sku_pattern = re.compile(
            r'(?:\"sku\"\s*:\s*\"([A-Z0-9\-]+)\"'
            r'|sku\s*:?\s*#?\s*([A-Z0-9\-]+))',
            re.IGNORECASE,
        )
        avail_pattern = re.compile(
            r'(?:in\s*stock|available|ships?\s*\w+)',
            re.IGNORECASE,
        )

        prices = price_pattern.findall(html)
        skus = sku_pattern.findall(html)
        in_stock = bool(avail_pattern.search(html))

        if prices and skus:
            sku_val = skus[0][0] or skus[0][1]
            raw = prices[0][0] or prices[0][1]
            price_val = float(raw.replace(",", ""))
            results.append(
                _make_result(
                    cls.VENDOR,
                    query,
                    sku=sku_val,
                    price=price_val,
                    availability="in_stock" if in_stock else "check_vendor",
                    lead_time="3-7 business days" if in_stock else "call_for_lead_time",
                    url=page_url,
                    confidence=0.55,
                )
            )
        elif prices:
            raw = prices[0][0] or prices[0][1]
            results.append(
                _make_result(
                    cls.VENDOR,
                    query,
                    price=float(raw.replace(",", "")),
                    availability="in_stock" if in_stock else "check_vendor",
                    url=page_url,
                    confidence=0.30,
                )
            )

        return results


# Registry of all adapters
_ADAPTERS = {
    "grainger": _GraingerAdapter,
    "graybar": _GraybarAdapter,
    "hdsupply": _HDSupplyAdapter,
}

SUPPORTED_VENDORS = list(_ADAPTERS.keys())


# ===================================================================
# Public tool functions
# ===================================================================

def vendor_price_search(
    query: str,
    vendors: list[str] | None = None,
    max_results: int = 5,
) -> dict:
    """Search configured vendors for pricing/availability of an item.

    Args:
        query: Product search term (e.g. "Cat6A patch cable 10ft blue").
        vendors: Optional list of vendor keys to query. Defaults to all.
        max_results: Maximum results to return across all vendors.

    Returns:
        Structured dict with ok, results[], and metadata.
    """
    if not query or not query.strip():
        return {"ok": False, "error": "query must be a non-empty string"}

    query = query.strip()
    target_vendors = vendors or SUPPORTED_VENDORS

    # Validate vendor names
    invalid = [v for v in target_vendors if v not in _ADAPTERS]
    if invalid:
        return {
            "ok": False,
            "error": f"Unknown vendor(s): {invalid}. Supported: {SUPPORTED_VENDORS}",
        }

    session = _http_session()
    all_results: list[dict] = []
    errors: list[dict] = []

    for vendor_key in target_vendors:
        adapter = _ADAPTERS[vendor_key]
        try:
            hits = adapter.fetch(query, session)
            if hits:
                all_results.extend(hits)
            else:
                all_results.append(_empty_result(vendor_key, query))
        except Exception as exc:
            logger.exception("Adapter %s raised unexpectedly", vendor_key)
            errors.append({"vendor": vendor_key, "error": str(exc)})
            all_results.append(_empty_result(vendor_key, query, reason="adapter_error"))

    # Sort by confidence descending, cap at max_results
    all_results.sort(key=lambda r: r.get("confidence", 0), reverse=True)
    all_results = all_results[:max_results]

    return {
        "ok": True,
        "query": query,
        "vendor_count": len(target_vendors),
        "result_count": len(all_results),
        "results": all_results,
        "errors": errors,
    }


def vendor_price_check(
    vendor: str,
    sku: str,
) -> dict:
    """Look up a specific SKU at a specific vendor.

    Args:
        vendor: Vendor key (grainger, graybar, hdsupply).
        sku: Product SKU / catalog number.

    Returns:
        Structured result dict.
    """
    if vendor not in _ADAPTERS:
        return {
            "ok": False,
            "error": f"Unknown vendor: {vendor}. Supported: {SUPPORTED_VENDORS}",
        }
    if not sku or not sku.strip():
        return {"ok": False, "error": "sku must be a non-empty string"}

    sku = sku.strip()
    session = _http_session()
    adapter = _ADAPTERS[vendor]

    try:
        hits = adapter.fetch(sku, session)
    except Exception as exc:
        logger.exception("vendor_price_check adapter error")
        return {
            "ok": True,
            "result": _empty_result(vendor, sku, reason="adapter_error"),
        }

    if not hits:
        return {
            "ok": True,
            "result": _empty_result(vendor, sku),
        }

    # Return best-confidence hit
    best = max(hits, key=lambda r: r.get("confidence", 0))
    return {"ok": True, "result": best}


def vendor_list_sources() -> dict:
    """List all configured vendor sources and their status.

    Returns:
        Dict with vendor keys, display names, and health status.
    """
    sources = []
    for key, adapter_cls in _ADAPTERS.items():
        sources.append({
            "vendor": key,
            "display_name": {
                "grainger": "Grainger Industrial Supply",
                "graybar": "Graybar Electric",
                "hdsupply": "HD Supply",
            }.get(key, key),
            "search_url_template": adapter_cls.SEARCH_URL,
            "status": "configured",
        })
    return {
        "ok": True,
        "vendor_count": len(sources),
        "vendors": sources,
        "rate_limit_rpm": RATE_LIMIT_RPM,
        "request_timeout_sec": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
    }
