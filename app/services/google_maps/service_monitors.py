"""
Monitor and webhook methods for GoogleMapsService.
"""

import logging
from typing import Any

# Logs under the facade module's name so log routing and filters keyed on
# ``app.services.google_maps_service`` are unaffected by the split.
logger = logging.getLogger("app.services.google_maps_service")


class MonitorsMixin:
    """Monitor and webhook methods of GoogleMapsService."""

    # =========================================================================
    # Monitors and webhooks
    #
    # These delegate to app.services.google_maps_monitors, which owns the
    # durable owner-scoped storage and the background scheduler. The service
    # layer only translates between the API's api_key and the store's owner id,
    # and between exceptions and the error dicts the router expects.
    # =========================================================================

    @staticmethod
    def _owner(api_key: str | None) -> str:
        """Map an API key to the storage owner id."""
        from app.services.google_maps_monitors import owner_id_for_api_key

        return owner_id_for_api_key(api_key)

    async def create_monitor(
        self,
        place_id: str | None = None,
        url: str | None = None,
        webhook_url: str | None = None,
        check_interval_hours: int = 24,
        track_fields: list[str] = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a monitor for a place.

        The monitor is persisted and scheduled before this returns, so the
        reported ``status`` describes durable state: a subsequent
        :meth:`get_monitor` on the returned id finds it. Previously this
        returned ``status: "active"`` for a monitor that was never stored.

        Args:
            place_id: Place to monitor (one of place_id/url required).
            url: Google Maps URL to monitor.
            webhook_url: Optional notification target, SSRF-validated.
            check_interval_hours: Hours between checks.
            track_fields: Fields to watch; defaults to a standard set.
            api_key: Caller's API key; scopes the monitor to that owner.

        Returns:
            The stored monitor, or an error dict.
        """
        from app.services import google_maps_monitors as monitors

        try:
            return await monitors.create_monitor(
                owner=self._owner(api_key),
                place_id=place_id,
                url=url,
                webhook_url=webhook_url,
                check_interval_hours=check_interval_hours,
                track_fields=track_fields,
            )
        except monitors.InvalidWebhookTarget as e:
            logger.warning(f"Monitor rejected: {e.reason}")
            return {"error": True, "status_code": 400, "message": e.public_message}
        except ValueError as e:
            return {"error": True, "status_code": 400, "message": str(e)}

    async def list_monitors(
        self, status: str | None = None, limit: int = 50, offset: int = 0, api_key: str | None = None
    ) -> dict[str, Any]:
        """List this caller's monitors. Never returns another caller's."""
        from app.services import google_maps_monitors as monitors

        return await monitors.list_monitors(owner=self._owner(api_key), status=status, limit=limit, offset=offset)

    async def get_monitor(
        self, monitor_id: str, include_history: bool = True, api_key: str | None = None
    ) -> dict[str, Any]:
        """Get one monitor, including its recorded snapshot history."""
        from app.services import google_maps_monitors as monitors

        try:
            monitor = await monitors.get_monitor(
                owner=self._owner(api_key),
                monitor_id=monitor_id,
                include_history=include_history,
            )
        except monitors.MonitorNotFound:
            return {"error": True, "status_code": 404, "message": "Monitor not found"}
        return {"monitor": monitor}

    async def delete_monitor(self, monitor_id: str, api_key: str | None = None) -> dict[str, Any]:
        """Delete one of this caller's monitors."""
        from app.services import google_maps_monitors as monitors

        try:
            await monitors.delete_monitor(owner=self._owner(api_key), monitor_id=monitor_id)
        except monitors.MonitorNotFound:
            return {"error": True, "status_code": 404, "message": "Monitor not found"}
        return {"deleted": True, "monitor_id": monitor_id}

    async def check_monitor_now(self, monitor_id: str, api_key: str | None = None) -> dict[str, Any]:
        """Run one monitor check immediately, outside the schedule."""
        from app.services import google_maps_monitors as monitors

        try:
            return await monitors.check_monitor(owner=self._owner(api_key), monitor_id=monitor_id)
        except monitors.MonitorNotFound:
            return {"error": True, "status_code": 404, "message": "Monitor not found"}

    async def register_webhook(
        self, url: str, events: list[str], secret: str | None = None, api_key: str | None = None
    ) -> dict[str, Any]:
        """
        Register a webhook that is actually delivered to.

        The target is SSRF-validated here and again before every delivery.
        Deliveries are HMAC-SHA256 signed with the returned secret, which is
        shown once and never echoed by :meth:`list_webhooks`.
        """
        from app.services import google_maps_monitors as monitors

        try:
            return await monitors.register_webhook(owner=self._owner(api_key), url=url, events=events, secret=secret)
        except monitors.InvalidWebhookTarget as e:
            logger.warning(f"Webhook target rejected: {e.reason}")
            return {"error": True, "status_code": 400, "message": e.public_message}
        except ValueError as e:
            return {"error": True, "status_code": 400, "message": str(e)}

    async def list_webhooks(self, limit: int = 50, offset: int = 0, api_key: str | None = None) -> dict[str, Any]:
        """List this caller's webhooks with their real delivery counters."""
        from app.services import google_maps_monitors as monitors

        return await monitors.list_webhooks(owner=self._owner(api_key), limit=limit, offset=offset)

    async def delete_webhook(self, webhook_id: str, api_key: str | None = None) -> dict[str, Any]:
        """Delete one of this caller's webhooks."""
        from app.services import google_maps_monitors as monitors

        try:
            await monitors.delete_webhook(owner=self._owner(api_key), webhook_id=webhook_id)
        except monitors.WebhookNotFound:
            return {"error": True, "status_code": 404, "message": "Webhook not found"}
        return {"deleted": True, "webhook_id": webhook_id}
