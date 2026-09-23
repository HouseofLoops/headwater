#!/usr/bin/env python3
"""
Environment validation script for Headwater.

This script checks for required environment variables and dependencies
to ensure the application can run properly.
"""

import importlib
import os
import socket
import sys
import time
from urllib.parse import urlparse

import requests

# ANSI color codes for terminal output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_header(message):
    """Print a formatted header message."""
    print(f"\n{BOLD}{BLUE}=== {message} ==={RESET}\n")


def print_success(message):
    """Print a success message."""
    print(f"{GREEN}✓ {message}{RESET}")


def print_warning(message):
    """Print a warning message."""
    print(f"{YELLOW}⚠ {message}{RESET}")


def print_error(message):
    """Print an error message."""
    print(f"{RED}✗ {message}{RESET}")


def check_env_variables():
    """Check for required environment variables."""
    print_header("Checking Environment Variables")

    # Required variables
    required_vars = [
        "API_KEYS",
    ]

    # Optional but recommended variables
    recommended_vars = [
        "ENABLE_API_KEY_AUTH",
        "RATE_LIMIT_ENABLED",
        "RATE_LIMIT_REQUESTS",
        "RATE_LIMIT_TIMEFRAME",
        "ENABLE_CACHE",
        "CACHE_TTL",
        "REDIS_URL",
        "ENABLE_PROXY",
        "PROXY_URLS",
        "CORS_ORIGINS",
        "SECRET_KEY",
    ]

    # API-specific variables
    api_vars = []

    # Check required variables
    missing_required = []
    for var in required_vars:
        if not os.environ.get(var):
            missing_required.append(var)
            print_error(f"Required variable {var} is not set")
        else:
            print_success(f"Required variable {var} is set")

    # Check recommended variables
    missing_recommended = []
    for var in recommended_vars:
        if not os.environ.get(var):
            missing_recommended.append(var)
            print_warning(f"Recommended variable {var} is not set")
        else:
            print_success(f"Recommended variable {var} is set")

    # Check API-specific variables
    api_status = {}
    for var in api_vars:
        if not os.environ.get(var):
            api_status[var] = False
            print_warning(f"API variable {var} is not set")
        else:
            api_status[var] = True
            print_success(f"API variable {var} is set")

    # Check proxy configuration
    if os.environ.get("ENABLE_PROXY", "").lower() == "true" and not os.environ.get("PROXY_URLS"):
        print_error("Proxy is enabled but PROXY_URLS is not set")

    return {"missing_required": missing_required, "missing_recommended": missing_recommended, "api_status": api_status}


def check_dependencies():
    """Check for required Python dependencies."""
    print_header("Checking Dependencies")

    required_packages = [
        "fastapi",
        "uvicorn",
        "requests",
        "pydantic",
        "pytest",
        "redis",
        "httpx",
    ]

    missing_packages = []
    for package in required_packages:
        try:
            importlib.import_module(package)
            print_success(f"Package {package} is installed")
        except ImportError:
            missing_packages.append(package)
            print_error(f"Package {package} is not installed")

    return missing_packages


def check_network():
    """Check network connectivity."""
    print_header("Checking Network Connectivity")

    # Check internet connectivity
    try:
        requests.get("https://google.com", timeout=5)
        print_success("Internet connection is available")
    except requests.RequestException:
        print_error("Internet connection is not available")

    # Check proxy if enabled
    if os.environ.get("ENABLE_PROXY", "").lower() == "true" and os.environ.get("PROXY_URLS"):
        proxy_url = os.environ.get("PROXY_URLS", "").split(",")[0].strip()
        try:
            # Parse proxy URL
            parsed = urlparse(proxy_url)
            proxy_host = parsed.hostname
            proxy_port = parsed.port or 80

            # Try to connect to proxy
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((proxy_host, proxy_port))
            if result == 0:
                print_success(f"Proxy server at {proxy_host}:{proxy_port} is reachable")
            else:
                print_error(f"Proxy server at {proxy_host}:{proxy_port} is not reachable")
            sock.close()

            # Try to make a request through the proxy
            proxies = {"http": proxy_url, "https": proxy_url}
            requests.get("https://google.com", proxies=proxies, timeout=5)
            print_success("Proxy connection is working")
        except Exception as e:
            print_error(f"Proxy connection error: {e!s}")


def check_api_services():
    """Check API services."""
    print_header("Checking API Services")

    # Check Redis if enabled
    if os.environ.get("ENABLE_CACHE") == "True" and os.environ.get("REDIS_URL"):
        try:
            import redis

            r = redis.from_url(os.environ.get("REDIS_URL"))
            r.ping()
            print_success("Redis connection is working")
        except Exception as e:
            print_error(f"Redis connection error: {e!s}")


def main():
    """Run all checks."""
    print(f"{BOLD}{BLUE}Headwater Environment Check{RESET}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python: {sys.version}")
    print(f"Platform: {sys.platform}")

    env_results = check_env_variables()
    dep_results = check_dependencies()
    check_network()
    check_api_services()

    print_header("Summary")

    if env_results["missing_required"]:
        print_error(f"Missing required environment variables: {', '.join(env_results['missing_required'])}")
    else:
        print_success("All required environment variables are set")

    if dep_results:
        print_error(f"Missing required packages: {', '.join(dep_results)}")
    else:
        print_success("All required packages are installed")

    if env_results["missing_recommended"]:
        print_warning(f"Missing recommended environment variables: {', '.join(env_results['missing_recommended'])}")

    print("\nFor more information, see the README.md file.")


if __name__ == "__main__":
    main()
