"""Failures must not be reported as successes.

The single most dangerous pattern in this codebase is an operation that fails
and returns a success-shaped response anyway -- `200 {"success": true,
"places": []}` is indistinguishable from a genuine empty result, so a total
outage looks like a valid negative answer and no alarm ever fires.

These tests cover three such paths found by an independent review after the
main remediation landed.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
class TestGridSearchDoesNotHideTotalFailure:
    """Every grid point failing is an outage, not an empty neighbourhood."""

    async def _run(self, side_effect):
        from app.services.google_maps_service import google_maps_service

        with (
            patch.object(google_maps_service, "_ensure_initialized", AsyncMock()),
            patch.object(google_maps_service, "search_and_wait", AsyncMock(side_effect=side_effect)),
        ):
            return await google_maps_service.grid_search(
                query="coffee",
                center_lat=45.0,
                center_lng=-122.0,
                radius_km=1.0,
                grid_size=2,
            )

    async def test_all_points_failing_is_an_error_not_an_empty_success(self):
        # Previously: {"success": True, "places": []} with the failures buried
        # in grid_metadata, which the router never inspected.
        result = await self._run(lambda **kw: {"error": True, "message": "upstream down"})

        assert result.get("error") is True, result
        assert result.get("status_code") == 502
        assert "success" not in result or result["success"] is not True

    async def test_all_points_raising_is_also_an_error(self):
        result = await self._run(RuntimeError("browser crashed"))

        assert result.get("error") is True, result
        assert result.get("status_code") == 502

    async def test_partial_failure_succeeds_but_is_flagged(self):
        calls = {"n": 0}

        async def _mixed(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"results": [{"place_id": "p1", "name": "Cafe"}]}
            return {"error": True, "message": "upstream down"}

        from app.services.google_maps_service import google_maps_service

        with (
            patch.object(google_maps_service, "_ensure_initialized", AsyncMock()),
            patch.object(google_maps_service, "search_and_wait", _mixed),
        ):
            result = await google_maps_service.grid_search(
                query="coffee",
                center_lat=45.0,
                center_lng=-122.0,
                radius_km=1.0,
                grid_size=2,
            )

        assert result.get("success") is True
        # The caller must be able to see that the answer is incomplete.
        assert result.get("partial") is True
        assert result.get("failed_grid_points", 0) > 0

    async def test_genuine_empty_result_still_succeeds(self):
        # No failures, just nothing there. This must NOT become an error.
        result = await self._run(lambda **kw: {"results": []})

        assert result.get("success") is True
        assert result.get("partial") is False
        assert result.get("places") == []


@pytest.mark.asyncio
class TestGeocodeGetReportsFailure:
    """batch_geocode returns an entry per address -- including failed ones."""

    async def _call(self, entry):
        from app.api.google_maps import geo

        with patch.object(geo, "batch_geocode", AsyncMock(return_value={"results": [entry]})):
            return await geo.geocode_get(
                address="123 Nowhere Street",
                api_key="k",
                rate_limit_check=None,
            )

    async def test_failed_address_is_not_reported_as_success(self):
        # The list is truthy even when its single entry failed, so checking
        # only `result.get("results")` wrapped a failure in "success": True.
        out = await self._call({"address": "123 Nowhere Street", "success": False, "error": "Address not found"})
        assert out["success"] is False
        assert out["error"] == "Address not found"

    async def test_successful_address_still_succeeds(self):
        out = await self._call({"address": "123 Nowhere Street", "success": True, "latitude": 1.0, "longitude": 2.0})
        assert out["success"] is True
        assert out["result"]["latitude"] == 1.0

    async def test_empty_results_is_a_failure(self):
        from app.api.google_maps import geo

        with patch.object(geo, "batch_geocode", AsyncMock(return_value={"results": []})):
            out = await geo.geocode_get(address="123 Nowhere Street", api_key="k", rate_limit_check=None)
        assert out["success"] is False
