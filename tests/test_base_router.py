"""
Tests for the BaseRouter class.

This module contains tests for the BaseRouter class in app/core/base_router.py.
"""
import pytest
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Optional
from app.core.base_router import BaseRouter
from app.core.auth import get_api_key as authenticate_api_key


def test_base_router_init():
    """Test BaseRouter initialization."""
    # Test with explicit service_name
    router = BaseRouter(
        prefix="/google-ads",
        service_name="google-ads-service"
    )
    assert router.service_name == "google-ads-service"
    
    # Test with auto-derived service_name
    router = BaseRouter(prefix="/google-ads")
    assert router.service_name == "google-ads"
    
    # Test with prefix without leading slash
    router = BaseRouter(prefix="google-ads")
    assert router.service_name == "google-ads"
    
    # Test with responses parameter
    custom_responses = {
        200: {"description": "Success"},
        400: {"description": "Bad Request"}
    }
    router = BaseRouter(
        prefix="/google-ads",
        responses=custom_responses
    )
    assert router.router.responses[200] == {"description": "Success"}
    assert router.router.responses[400] == {"description": "Bad Request"}


def test_extract_service_name():
    """Test _extract_service_name method."""
    router = BaseRouter(prefix="/test")
    
    # Test simple prefix
    assert router._extract_service_name("/google-ads") == "google-ads"
    
    # Test prefix with multiple segments
    assert router._extract_service_name("/youtube-transcripts") == "youtube-transcripts"
    
    # Test API versioned prefix
    assert router._extract_service_name("/api/v1/google-ads") == "google-ads"
    
    # Test API versioned prefix with no service
    with pytest.raises(ValueError):
        router._extract_service_name("/api/v1/")
    
    # Test empty prefix
    with pytest.raises(ValueError):
        router._extract_service_name("")
    
    # Test prefix with only slash
    with pytest.raises(ValueError):
        router._extract_service_name("/")


def test_create_error_detail():
    """Test _create_error_detail method."""
    router = BaseRouter(prefix="/test")
    
    # Test basic error detail
    error = router._create_error_detail(
        status=400,
        title="Bad Request",
        detail="Invalid parameters",
        type="validation_error"
    )
    assert error["status"] == 400
    assert error["title"] == "Bad Request"
    assert error["detail"] == "Invalid parameters"
    assert error["type"] == "https://headwater.com/problems/validation_error"
    
    # Test with instance
    error = router._create_error_detail(
        status=404,
        title="Not Found",
        detail="Resource not found",
        type="not_found",
        instance="/api/v1/resources/123"
    )
    assert error["instance"] == "/api/v1/resources/123"
    
    # Test with additional fields
    error = router._create_error_detail(
        status=422,
        title="Validation Error",
        detail="Invalid field value",
        type="validation_error",
        field="name",
        code="invalid_value"
    )
    assert error["field"] == "name"
    assert error["code"] == "invalid_value"
    
    # Test with full URI type
    error = router._create_error_detail(
        status=500,
        title="Server Error",
        detail="Internal error",
        type="https://example.com/errors/server_error"
    )
    assert error["type"] == "https://example.com/errors/server_error"


def test_raise_http_exception():
    """Test raise_http_exception method."""
    router = BaseRouter(prefix="/test")
    
    # Test basic exception
    with pytest.raises(HTTPException) as excinfo:
        router.raise_http_exception(
            status_code=400,
            detail="Invalid parameters"
        )
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["detail"] == "Invalid parameters"
    assert excinfo.value.detail["title"] == "Bad Request"
    assert excinfo.value.detail["status"] == 400
    
    # Test with custom title and type
    with pytest.raises(HTTPException) as excinfo:
        router.raise_http_exception(
            status_code=403,
            detail="Access denied",
            title="Permission Error",
            type="permission_error"
        )
    assert excinfo.value.detail["title"] == "Permission Error"
    assert excinfo.value.detail["type"] == "https://headwater.com/problems/permission_error"
    
    # Test with headers
    with pytest.raises(HTTPException) as excinfo:
        router.raise_http_exception(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": "60"}
        )
    assert excinfo.value.headers["Retry-After"] == "60"
    assert excinfo.value.headers["Content-Type"] == "application/problem+json"


def test_convenience_methods():
    """Test convenience methods for raising exceptions."""
    router = BaseRouter(prefix="/test")
    
    # Test raise_validation_error
    with pytest.raises(HTTPException) as excinfo:
        router.raise_validation_error("Invalid email format", field="email")
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["detail"] == "Invalid email format"
    assert excinfo.value.detail["field"] == "email"
    
    # Test raise_not_found_error
    with pytest.raises(HTTPException) as excinfo:
        router.raise_not_found_error("User", "123")
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail["detail"] == "User with identifier '123' not found"
    
    # Test raise_internal_error
    with pytest.raises(HTTPException) as excinfo:
        router.raise_internal_error("Database connection failed")
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail["detail"] == "Database connection failed"
    
    # Test raise_internal_error with default message
    with pytest.raises(HTTPException) as excinfo:
        router.raise_internal_error()
    assert excinfo.value.detail["detail"] == "An unexpected error occurred"
