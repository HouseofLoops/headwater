"""Place monitors and webhook registrations.

Mounted onto ``google_maps_router`` by this package's ``__init__``; the paths
declared here are relative to the ``/google-maps`` prefix applied there.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.google_maps.common import (
    INTERNAL_ERROR_DETAIL,
    SafeUrlValidationRoute,
    upstream_error,
)
from app.api.google_maps.schemas import (
    MonitorRequest,
    WebhookRequest,
)
from app.core.auth import get_api_key
from app.core.log_safety import scrub
from app.core.rate_limiter import rate_limit
from app.services.google_maps_service import google_maps_service

logger = logging.getLogger(__name__)

router = APIRouter(route_class=SafeUrlValidationRoute)


@router.post(
    "/monitors",
    summary="Create place monitor",
    response_description="Monitor creation status"
)
async def create_monitor(
    request: MonitorRequest,
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Create a monitor to track changes to a place.

    **Trackable Fields:**
    - `rating` - Average rating changes
    - `review_count` - New reviews
    - `hours` - Operating hours changes
    - `phone` - Phone number changes
    - `website` - Website URL changes
    - `status` - Open/closed status

    **Notifications:**
    Provide a `webhook_url` to receive notifications when changes are detected.
    """
    if not request.place_id and not request.url:
        raise HTTPException(
            status_code=400,
            detail="Either 'place_id' or 'url' must be provided"
        )

    logger.info(
        "Create monitor for place: %s", scrub(request.place_id or request.url)
    )

    try:
        result = await google_maps_service.create_monitor(
            place_id=request.place_id,
            url=request.url,
            webhook_url=request.webhook_url,
            check_interval_hours=request.check_interval_hours,
            track_fields=request.track_fields,
            api_key=api_key
        )

        if result.get("error"):
            raise upstream_error(result, "Failed to create monitor")

        return {
            "success": True,
            "monitor_id": result.get("monitor_id"),
            "place_id": result.get("place_id"),
            "status": "active",
            "check_interval_hours": request.check_interval_hours,
            "track_fields": request.track_fields,
            "next_check": result.get("next_check"),
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create monitor error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.get(
    "/monitors",
    summary="List monitors",
    response_description="Active monitors"
)
async def list_monitors(
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=100, description="Maximum monitors"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    List all place monitors.

    **Status Filters:**
    - `active` - Currently monitoring
    - `paused` - Temporarily paused
    - `deleted` - Marked for deletion
    """
    try:
        result = await google_maps_service.list_monitors(
            status=status,
            limit=limit,
            offset=offset,
            api_key=api_key
        )

        if result.get("error"):
            raise upstream_error(result, "Failed to list monitors")

        return {
            "success": True,
            "monitors": result.get("monitors", []),
            "total": result.get("total", 0),
            "pagination": {
                "limit": limit,
                "offset": offset
            },
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List monitors error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.get(
    "/monitors/{monitor_id}",
    summary="Get monitor status",
    response_description="Monitor details and history"
)
async def get_monitor(
    monitor_id: str = Path(..., description="Monitor ID"),
    include_history: bool = Query(True, description="Include change history"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Get details and change history for a specific monitor.
    """
    try:
        result = await google_maps_service.get_monitor(
            monitor_id=monitor_id,
            include_history=include_history,
            api_key=api_key
        )

        if result.get("error"):
            if result.get("status_code") == 404:
                raise HTTPException(status_code=404, detail="Monitor not found")
            raise upstream_error(result, "Failed to get monitor")

        return {
            "success": True,
            "monitor": result.get("monitor"),
            "history": result.get("history", []) if include_history else None,
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get monitor error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.delete(
    "/monitors/{monitor_id}",
    summary="Delete monitor",
    response_description="Deletion confirmation"
)
async def delete_monitor(
    monitor_id: str = Path(..., description="Monitor ID"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Delete a place monitor.
    """
    try:
        result = await google_maps_service.delete_monitor(monitor_id, api_key=api_key)

        if result.get("error"):
            if result.get("status_code") == 404:
                raise HTTPException(status_code=404, detail="Monitor not found")
            raise upstream_error(result, "Failed to delete monitor")

        return {
            "success": True,
            "monitor_id": monitor_id,
            "message": "Monitor deleted successfully",
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete monitor error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.post(
    "/webhooks",
    summary="Register webhook",
    response_description="Webhook registration"
)
async def register_webhook(
    request: WebhookRequest,
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Register a webhook to receive notifications.

    **Available Events:**
    - `job.completed` - Search job completed
    - `job.failed` - Search job failed
    - `monitor.changed` - Monitored place changed

    **Webhook Payload:**
    ```json
    {
        "event": "job.completed",
        "timestamp": "2024-01-15T10:30:00Z",
        "data": { ... }
    }
    ```
    """
    logger.info("Register webhook: %s", scrub(request.url))

    try:
        result = await google_maps_service.register_webhook(
            url=request.url,
            events=request.events,
            secret=request.secret,
            api_key=api_key
        )

        if result.get("error"):
            raise upstream_error(result, "Failed to register webhook")

        return {
            "success": True,
            "webhook_id": result.get("webhook_id"),
            "url": request.url,
            "events": request.events,
            "status": "active",
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Register webhook error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.get(
    "/webhooks",
    summary="List webhooks",
    response_description="Registered webhooks"
)
async def list_webhooks(
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    List all registered webhooks.
    """
    try:
        result = await google_maps_service.list_webhooks(api_key=api_key)

        if result.get("error"):
            raise upstream_error(result, "Failed to list webhooks")

        return {
            "success": True,
            "webhooks": result.get("webhooks", []),
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List webhooks error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.delete(
    "/webhooks/{webhook_id}",
    summary="Delete webhook",
    response_description="Deletion confirmation"
)
async def delete_webhook(
    webhook_id: str = Path(..., description="Webhook ID"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Delete a registered webhook.
    """
    try:
        result = await google_maps_service.delete_webhook(webhook_id, api_key=api_key)

        if result.get("error"):
            if result.get("status_code") == 404:
                raise HTTPException(status_code=404, detail="Webhook not found")
            raise upstream_error(result, "Failed to delete webhook")

        return {
            "success": True,
            "webhook_id": webhook_id,
            "message": "Webhook deleted successfully",
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete webhook error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)
