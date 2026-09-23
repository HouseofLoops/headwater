"""Asynchronous scrape job management.

Every route here derives an owner id from the caller's API key and passes
it to the store, so a caller cannot address another caller's job at all.
A cross-owner read is 404, never 403: a 403 would confirm the id exists.

Mounted onto ``google_maps_router`` by this package's ``__init__``; the paths
declared here are relative to the ``/google-maps`` prefix applied there.
"""
import csv
import io
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import StreamingResponse

from app.api.google_maps.common import (
    INTERNAL_ERROR_DETAIL,
    SafeUrlValidationRoute,
    places_from_result,
    upstream_error,
)
from app.api.google_maps.schemas import (
    ExportFormat,
)
from app.core.auth import get_api_key
from app.core.log_safety import scrub
from app.core.rate_limiter import rate_limit
from app.services.google_maps_service import google_maps_service
from app.services.record_store import owner_id_for_api_key

logger = logging.getLogger(__name__)

router = APIRouter(route_class=SafeUrlValidationRoute)


@router.get(
    "/jobs",
    summary="List all scraping jobs",
    response_description="List of jobs with their status"
)
async def list_jobs(
    status: str | None = Query(
        None,
        description="Filter by status (pending, running, completed, failed)"
    ),
    limit: int = Query(50, ge=1, le=100, description="Maximum jobs to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    List the calling API key's scraping jobs, with optional status filtering.

    Only jobs created by this API key are ever returned.
    """
    result = await google_maps_service.list_jobs(
        owner=owner_id_for_api_key(api_key),
        status=status,
        limit=limit,
        offset=offset
    )

    # gosom returns a list on success, dict on error
    if isinstance(result, dict) and result.get("error"):
        raise upstream_error(result, "Failed to list jobs")

    # If result is a list, use it directly as jobs
    jobs = result if isinstance(result, list) else result.get("jobs", [])

    return {
        "success": True,
        "jobs": jobs,
        "total": len(jobs),
        "limit": limit,
        "offset": offset,
        "timestamp": datetime.now().isoformat()
    }


@router.get(
    "/jobs/{job_id}",
    summary="Get job status",
    response_description="Job status and progress information"
)
async def get_job_status(
    job_id: str = Path(..., description="Job ID to check"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Get the status of one of this API key's scraping jobs.

    A job belonging to another caller is reported as not found.
    """
    result = await google_maps_service.get_job_status(
        job_id, owner=owner_id_for_api_key(api_key)
    )

    if result.get("error"):
        if result.get("status_code") == 404:
            raise HTTPException(status_code=404, detail="Job not found")
        raise upstream_error(result, "Failed to get job status")

    return {
        "success": True,
        "job_id": job_id,
        "status": result.get("status"),
        "progress": result.get("progress"),
        "details": result,
        "timestamp": datetime.now().isoformat()
    }


@router.get(
    "/jobs/{job_id}/results",
    summary="Get job results",
    response_description="Search results from completed job"
)
async def get_job_results(
    job_id: str = Path(..., description="Job ID to get results for"),
    format: str = Query("json", description="Output format (json, csv)"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Get the results of one of this API key's completed scraping jobs.

    A job belonging to another caller is reported as not found.

    Returns comprehensive place data including:
    - Basic info (name, address, phone, website)
    - Reviews and ratings with star breakdown
    - Service options (dine-in, drive-through, delivery)
    - Review topics and keywords with mention counts
    - Operating hours and open/closed status
    - Menu, order, and reservation links
    - Related places ("People also search for")
    """
    owner = owner_id_for_api_key(api_key)

    # First check job status
    status_result = await google_maps_service.get_job_status(job_id, owner=owner)

    if status_result.get("error"):
        if status_result.get("status_code") == 404:
            raise HTTPException(status_code=404, detail="Job not found")
        raise upstream_error(status_result, "Failed to get job status")

    job_status = status_result.get("status", "").lower()

    if job_status == "pending" or job_status == "running":
        return {
            "success": False,
            "job_id": job_id,
            "status": job_status,
            "message": "Job is still running. Please check back later.",
            "progress": status_result.get("progress"),
            "timestamp": datetime.now().isoformat()
        }

    if job_status == "failed":
        # This used to interpolate ``status_result['error']`` into the detail,
        # which could only ever render "Unknown error": a truthy ``error`` is
        # already consumed by the check above, so the branch is only reached
        # when the key is absent. Made explicit, and the reason -- which names
        # browser paths and proxy hosts -- is logged rather than returned.
        logger.warning(
                "Job %s reported status 'failed': %s",
                scrub(job_id), scrub(status_result),
            )
        raise HTTPException(status_code=500, detail="Job failed.")

    # Get results
    result = await google_maps_service.get_job_results(
        job_id, owner=owner, format=format
    )

    if result.get("error"):
        raise upstream_error(result, "Failed to get job results")

    if format == "csv":
        return {
            "success": True,
            "job_id": job_id,
            "format": "csv",
            "data": result.get("data"),
            "timestamp": datetime.now().isoformat()
        }

    # Process JSON results
    places = places_from_result(result, f"job {job_id} results")

    return {
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "total_results": len(places),
        "places": places,
        "timestamp": datetime.now().isoformat()
    }


@router.delete(
    "/jobs/{job_id}",
    summary="Delete a job",
    response_description="Deletion confirmation"
)
async def delete_job(
    job_id: str = Path(..., description="Job ID to delete"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Delete one of this API key's jobs and its results.

    A job belonging to another caller is reported as not found and is not
    deleted.
    """
    result = await google_maps_service.delete_job(
        job_id, owner=owner_id_for_api_key(api_key)
    )

    if result.get("error"):
        if result.get("status_code") == 404:
            raise HTTPException(status_code=404, detail="Job not found")
        raise upstream_error(result, "Failed to delete job")

    return {
        "success": True,
        "job_id": job_id,
        "message": "Job deleted successfully",
        "timestamp": datetime.now().isoformat()
    }


@router.get(
    "/jobs/{job_id}/export",
    summary="Export job results",
    response_description="Results in specified format"
)
async def export_job_results(
    job_id: str = Path(..., description="Job ID"),
    format: ExportFormat = Query(ExportFormat.JSON, description="Export format"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Export one of this API key's job results in various formats.

    A job belonging to another caller is reported as not found: export is a
    read of job data and is scoped exactly like `/jobs/{job_id}/results`.

    **Supported Formats:**
    - `json` - Standard JSON (default)
    - `csv` - Comma-separated values
    - `xlsx` - Microsoft Excel
    - `jsonl` - JSON Lines (one record per line)

    For CSV and Excel, complex fields (like hours, reviews) are serialized as JSON strings.
    """
    logger.info("Export job %s as %s", scrub(job_id), scrub(format.value))

    try:
        # Get job results
        result = await google_maps_service.get_job_results(
            job_id, owner=owner_id_for_api_key(api_key)
        )

        if result.get("error"):
            if result.get("status_code") == 404:
                raise HTTPException(status_code=404, detail="Job not found")
            raise upstream_error(result, "Failed to get results")

        places = places_from_result(result, f"job {job_id} export")

        if format == ExportFormat.CSV:
            # Generate CSV
            output = io.StringIO()
            if places:
                # Flatten nested fields for CSV
                flat_places = []
                for p in places:
                    flat = {}
                    for k, v in p.items():
                        if isinstance(v, (dict, list)):
                            flat[k] = json.dumps(v) if v else ""
                        else:
                            flat[k] = v if v is not None else ""
                    flat_places.append(flat)

                writer = csv.DictWriter(output, fieldnames=flat_places[0].keys())
                writer.writeheader()
                writer.writerows(flat_places)

            return StreamingResponse(
                iter([output.getvalue()]),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=results_{job_id}.csv"}
            )

        elif format == ExportFormat.JSON_LINES:
            # Generate JSON Lines
            lines = [json.dumps(p) for p in places]
            content = "\n".join(lines)
            return StreamingResponse(
                iter([content]),
                media_type="application/x-ndjson",
                headers={"Content-Disposition": f"attachment; filename=results_{job_id}.jsonl"}
            )

        elif format == ExportFormat.EXCEL:
            # For Excel, we'll return JSON with a note (full Excel would need openpyxl)
            return {
                "success": True,
                "job_id": job_id,
                "format": "xlsx",
                "message": "Excel export - use CSV format and import to Excel, or integrate openpyxl for native xlsx",
                "data_preview": places[:5],
                "total_records": len(places),
                "timestamp": datetime.now().isoformat()
            }

        else:
            # JSON format
            return {
                "success": True,
                "job_id": job_id,
                "format": "json",
                "total_results": len(places),
                "places": places,
                "timestamp": datetime.now().isoformat()
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Export error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)
