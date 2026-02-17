"""Acceptance tests for PR6: Vendor pricing tools.

Validates:
  - vendor_price_search returns at least one vendor result for a common item
  - No tool returns null content; always JSON with stable schema
  - vendor_price_check returns structured result for a known SKU
  - vendor_list_sources returns all 3 configured vendors
  - All vendor tools registered in /tools endpoint
  - Rate limiter, retry/backoff, and User-Agent safeguards are active
  - Error cases produce stable JSON (never null)
"""

import importlib
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def _workspace_root(tmp_path, monkeypatch):
    """Set up a temp WORKSPACE_ROOT so the app can initialize."""
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    ws_dir = ws_root / "pricing-proj"
    ws_dir.mkdir()

    monkeypatch.setenv("WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("ROUTER_AUTH_TOKEN", "")
    monkeypatch.setenv("VENDOR_RATE_LIMIT_RPM", "600")  # high limit for tests

    from router.app import workspace_tools, vendor_pricing_tools, tools, main
    importlib.reload(vendor_pricing_tools)
    importlib.reload(workspace_tools)
    importlib.reload(tools)
    importlib.reload(main)

    yield str(ws_root)


@pytest.fixture()
def client():
    from router.app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _mock_grainger_html():
    """Realistic HTML fragment that Grainger adapter can parse."""
    return '''
    <html><body>
    <script type="application/ld+json">
    {"@type": "Product", "sku": "6JD71", "name": "Cat6A Patch Cable",
     "offers": {"price": "12.49", "availability": "InStock"}}
    </script>
    <div>In Stock - ships in 1-3 business days</div>
    <span>item #: 6JD71</span>
    <span>"price": "12.49"</span>
    </body></html>
    '''


def _mock_graybar_html():
    """Realistic HTML fragment for Graybar adapter."""
    return '''
    <html><body>
    <div class="product" data-sku="CAT6A-10BL">
        <span>"sku": "CAT6A-10BL"</span>
        <span>"price": "14.75"</span>
        <div>In Stock</div>
        <span>catalog #: CAT6A-10BL</span>
    </div>
    </body></html>
    '''


def _mock_hdsupply_html():
    """Realistic HTML fragment for HD Supply adapter."""
    return '''
    <html><body>
    <div class="product-card">
        <span>"sku": "HDS-C6A10"</span>
        <span>"price": "11.99"</span>
        <div>Available - ships in 3-5 days</div>
    </div>
    </body></html>
    '''


def _mock_empty_html():
    """HTML with no product data."""
    return '<html><body><p>No results found</p></body></html>'


def _mock_session_factory(grainger_html=None, graybar_html=None, hdsupply_html=None):
    """Build a mock requests.Session that returns canned HTML per vendor."""
    vendor_html = {
        "grainger.com": grainger_html or _mock_empty_html(),
        "graybar.com": graybar_html or _mock_empty_html(),
        "hdsupply.com": hdsupply_html or _mock_empty_html(),
    }

    def _mock_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        for domain, html in vendor_html.items():
            if domain in url:
                resp.text = html
                return resp
        resp.text = _mock_empty_html()
        return resp

    session = MagicMock()
    session.get = _mock_get
    return session


# =====================================================================
# Test: Query a common item returns at least one vendor result
# =====================================================================

class TestVendorPriceSearch:
    """vendor_price_search returns structured results for common queries."""

    def test_common_item_returns_results(self, client):
        """ACCEPTANCE: Query a common item returns at least one vendor result."""
        with patch("router.app.vendor_pricing_tools._http_session") as mock_session:
            mock_session.return_value = _mock_session_factory(
                grainger_html=_mock_grainger_html(),
                graybar_html=_mock_graybar_html(),
                hdsupply_html=_mock_hdsupply_html(),
            )

            resp = client.post("/tool", json={
                "tool_call": {
                    "name": "vendor_price_search",
                    "arguments": {"query": "Cat6A patch cable 10ft"},
                }
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["result_count"] >= 1
        assert len(body["results"]) >= 1

        # At least one result should have a real price
        priced = [r for r in body["results"] if r["price"] is not None]
        assert len(priced) >= 1

    def test_search_filters_by_vendor(self, client):
        """Can restrict search to specific vendors."""
        with patch("router.app.vendor_pricing_tools._http_session") as mock_session:
            mock_session.return_value = _mock_session_factory(
                grainger_html=_mock_grainger_html(),
            )

            resp = client.post("/tool", json={
                "tool_call": {
                    "name": "vendor_price_search",
                    "arguments": {
                        "query": "Cat6A cable",
                        "vendors": ["grainger"],
                    },
                }
            })

        body = resp.json()
        assert body["ok"] is True
        assert body["vendor_count"] == 1
        vendors_in_results = {r["vendor"] for r in body["results"]}
        assert vendors_in_results == {"grainger"}

    def test_empty_query_returns_error(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "vendor_price_search",
                "arguments": {"query": ""},
            }
        })
        body = resp.json()
        assert body["ok"] is False
        assert "query" in body["error"].lower()

    def test_invalid_vendor_returns_error(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "vendor_price_search",
                "arguments": {"query": "cable", "vendors": ["fake_vendor"]},
            }
        })
        body = resp.json()
        assert body["ok"] is False
        assert "fake_vendor" in body["error"]

    def test_max_results_respected(self, client):
        with patch("router.app.vendor_pricing_tools._http_session") as mock_session:
            mock_session.return_value = _mock_session_factory(
                grainger_html=_mock_grainger_html(),
                graybar_html=_mock_graybar_html(),
                hdsupply_html=_mock_hdsupply_html(),
            )

            resp = client.post("/tool", json={
                "tool_call": {
                    "name": "vendor_price_search",
                    "arguments": {"query": "cable", "max_results": 2},
                }
            })

        body = resp.json()
        assert body["ok"] is True
        assert len(body["results"]) <= 2


# =====================================================================
# Test: No tool returns null content; always JSON
# =====================================================================

class TestNoNullContent:
    """ACCEPTANCE: No tool returns null content; always JSON."""

    def test_search_no_results_still_json(self, client):
        """When vendors return no products, still get valid JSON."""
        with patch("router.app.vendor_pricing_tools._http_session") as mock_session:
            mock_session.return_value = _mock_session_factory()  # all empty

            resp = client.post("/tool", json={
                "tool_call": {
                    "name": "vendor_price_search",
                    "arguments": {"query": "xyznonexistent123"},
                }
            })

        body = resp.json()
        assert body is not None
        assert body["ok"] is True
        assert body["results"] is not None
        assert isinstance(body["results"], list)
        # Each result has all required fields, none are None at top level
        for result in body["results"]:
            assert result["vendor"] is not None
            assert isinstance(result["vendor"], str)
            assert result["availability"] is not None
            assert result["confidence"] is not None
            assert "sku" in result
            assert "price" in result
            assert "leadTime" in result
            assert "url" in result

    def test_price_check_no_match_still_json(self, client):
        """SKU not found returns stable JSON, not null."""
        with patch("router.app.vendor_pricing_tools._http_session") as mock_session:
            mock_session.return_value = _mock_session_factory()

            resp = client.post("/tool", json={
                "tool_call": {
                    "name": "vendor_price_check",
                    "arguments": {"vendor": "grainger", "sku": "NONEXISTENT999"},
                }
            })

        body = resp.json()
        assert body is not None
        assert body["ok"] is True
        assert body["result"] is not None
        result = body["result"]
        assert result["vendor"] == "grainger"
        assert result["sku"] == ""
        assert result["availability"] == "unknown"
        assert result["confidence"] == 0.0
        assert result["reason"] == "no_match"

    def test_list_sources_always_json(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "vendor_list_sources",
                "arguments": {},
            }
        })
        body = resp.json()
        assert body is not None
        assert body["ok"] is True
        assert body["vendors"] is not None
        assert isinstance(body["vendors"], list)

    def test_adapter_exception_produces_stable_json(self, client):
        """Even if an adapter raises, we still get valid JSON."""
        with patch("router.app.vendor_pricing_tools._http_session") as mock_session:
            session = MagicMock()
            session.get.side_effect = Exception("network boom")
            mock_session.return_value = session

            resp = client.post("/tool", json={
                "tool_call": {
                    "name": "vendor_price_search",
                    "arguments": {"query": "cable"},
                }
            })

        body = resp.json()
        assert body is not None
        assert body["ok"] is True
        assert isinstance(body["results"], list)
        # All results should be empty/error results, still valid JSON
        for result in body["results"]:
            assert result["vendor"] is not None
            assert result["confidence"] == 0.0


# =====================================================================
# Test: vendor_price_check structured result
# =====================================================================

class TestVendorPriceCheck:
    def test_known_sku_returns_result(self, client):
        with patch("router.app.vendor_pricing_tools._http_session") as mock_session:
            mock_session.return_value = _mock_session_factory(
                grainger_html=_mock_grainger_html(),
            )

            resp = client.post("/tool", json={
                "tool_call": {
                    "name": "vendor_price_check",
                    "arguments": {"vendor": "grainger", "sku": "6JD71"},
                }
            })

        body = resp.json()
        assert body["ok"] is True
        result = body["result"]
        assert result["vendor"] == "grainger"
        assert result["price"] is not None
        assert result["confidence"] > 0

    def test_schema_fields_present(self, client):
        """Result has all required schema fields."""
        with patch("router.app.vendor_pricing_tools._http_session") as mock_session:
            mock_session.return_value = _mock_session_factory(
                graybar_html=_mock_graybar_html(),
            )

            resp = client.post("/tool", json={
                "tool_call": {
                    "name": "vendor_price_check",
                    "arguments": {"vendor": "graybar", "sku": "CAT6A-10BL"},
                }
            })

        body = resp.json()
        result = body["result"]
        required_fields = {"vendor", "sku", "price", "availability", "leadTime", "url", "confidence"}
        assert required_fields.issubset(set(result.keys()))

    def test_invalid_vendor_returns_error(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "vendor_price_check",
                "arguments": {"vendor": "fake", "sku": "ABC123"},
            }
        })
        body = resp.json()
        assert body["ok"] is False
        assert "fake" in body["error"]

    def test_empty_sku_returns_error(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "vendor_price_check",
                "arguments": {"vendor": "grainger", "sku": ""},
            }
        })
        body = resp.json()
        assert body["ok"] is False
        assert "sku" in body["error"].lower()


# =====================================================================
# Test: vendor_list_sources
# =====================================================================

class TestVendorListSources:
    def test_lists_all_three_vendors(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "vendor_list_sources",
                "arguments": {},
            }
        })
        body = resp.json()
        assert body["ok"] is True
        assert body["vendor_count"] == 3
        vendor_keys = {v["vendor"] for v in body["vendors"]}
        assert vendor_keys == {"grainger", "graybar", "hdsupply"}

    def test_vendor_entries_have_required_fields(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "vendor_list_sources",
                "arguments": {},
            }
        })
        body = resp.json()
        for vendor in body["vendors"]:
            assert "vendor" in vendor
            assert "display_name" in vendor
            assert "search_url_template" in vendor
            assert "status" in vendor

    def test_config_values_present(self, client):
        resp = client.post("/tool", json={
            "tool_call": {
                "name": "vendor_list_sources",
                "arguments": {},
            }
        })
        body = resp.json()
        assert "rate_limit_rpm" in body
        assert "request_timeout_sec" in body
        assert "max_retries" in body
        assert isinstance(body["rate_limit_rpm"], int)


# =====================================================================
# Test: Tools registered in /tools endpoint
# =====================================================================

class TestToolRegistration:
    def test_all_vendor_tools_in_schema(self, client):
        resp = client.get("/tools")
        assert resp.status_code == 200
        names = [t["function"]["name"] for t in resp.json()["tools"]]
        assert "vendor_price_search" in names
        assert "vendor_price_check" in names
        assert "vendor_list_sources" in names

    def test_vendor_tools_have_required_params(self, client):
        resp = client.get("/tools")
        tools_by_name = {
            t["function"]["name"]: t["function"] for t in resp.json()["tools"]
        }

        search = tools_by_name["vendor_price_search"]
        assert "query" in search["parameters"]["required"]

        check = tools_by_name["vendor_price_check"]
        assert "vendor" in check["parameters"]["required"]
        assert "sku" in check["parameters"]["required"]

        sources = tools_by_name["vendor_list_sources"]
        assert sources["parameters"]["required"] == []


# =====================================================================
# Test: Safeguards — rate limiter, user-agent, retry
# =====================================================================

class TestSafeguards:
    def test_rate_limiter_enforces_delay(self):
        """Rate limiter introduces delay between calls to same vendor."""
        from router.app.vendor_pricing_tools import _RateLimiter

        limiter = _RateLimiter(rpm=6000)  # 10ms interval
        limiter.wait("test_vendor")
        t0 = time.monotonic()
        limiter.wait("test_vendor")
        elapsed = time.monotonic() - t0
        # Should have waited ~10ms (allow some slack)
        assert elapsed >= 0.005

    def test_rate_limiter_independent_per_vendor(self):
        """Different vendors have independent rate limit buckets."""
        from router.app.vendor_pricing_tools import _RateLimiter

        limiter = _RateLimiter(rpm=60)  # 1s interval
        limiter.wait("vendor_a")
        t0 = time.monotonic()
        limiter.wait("vendor_b")  # different vendor, no wait
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5  # should be near-instant

    def test_user_agent_header_set(self):
        """HTTP session has User-Agent header set."""
        from router.app.vendor_pricing_tools import _http_session, USER_AGENT

        session = _http_session()
        assert session.headers.get("User-Agent") == USER_AGENT
        assert "AgentStack" in USER_AGENT

    def test_retry_adapter_configured(self):
        """HTTP session has retry adapter mounted."""
        from router.app.vendor_pricing_tools import _http_session

        session = _http_session()
        adapter = session.get_adapter("https://example.com")
        assert adapter.max_retries.total >= 2
        assert 429 in adapter.max_retries.status_forcelist
        assert 503 in adapter.max_retries.status_forcelist

    def test_request_timeout_configurable(self, monkeypatch):
        """REQUEST_TIMEOUT env var is respected."""
        monkeypatch.setenv("VENDOR_REQUEST_TIMEOUT", "42")
        from router.app import vendor_pricing_tools
        importlib.reload(vendor_pricing_tools)
        assert vendor_pricing_tools.REQUEST_TIMEOUT == 42


# =====================================================================
# Test: Result schema consistency
# =====================================================================

class TestResultSchema:
    """Every result dict has the same shape regardless of vendor/match."""

    REQUIRED_KEYS = {"vendor", "sku", "price", "availability", "leadTime", "url", "confidence", "query"}

    def test_match_result_has_all_keys(self):
        from router.app.vendor_pricing_tools import _make_result
        result = _make_result("grainger", "test", sku="A", price=1.0)
        assert self.REQUIRED_KEYS.issubset(set(result.keys()))

    def test_empty_result_has_all_keys(self):
        from router.app.vendor_pricing_tools import _empty_result
        result = _empty_result("grainger", "test")
        assert self.REQUIRED_KEYS.issubset(set(result.keys()))
        assert result["confidence"] == 0.0
        assert result["sku"] == ""
        assert result["availability"] == "unknown"

    def test_empty_result_has_reason(self):
        from router.app.vendor_pricing_tools import _empty_result
        result = _empty_result("graybar", "q", reason="timeout")
        assert result["reason"] == "timeout"
