"""
URL query-string parsing and building helpers.

Split out of app.core.utils, which still re-exports every name here.
"""

from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse


def parse_query_params(url: str) -> dict[str, list[str]]:
    """
    Parse query parameters from a URL.
    
    Args:
        url: The URL to parse
        
    Returns:
        Dict[str, List[str]]: Dictionary of query parameters
    """
    parsed_url = urlparse(url)
    return parse_qs(parsed_url.query)


def build_url(
    base_url: str,
    path: str | None = None,
    params: dict[str, Any] | None = None
) -> str:
    """
    Build a URL with path and query parameters.
    
    Args:
        base_url: The base URL
        path: Optional path to append
        params: Optional query parameters
        
    Returns:
        str: The built URL
    """
    url = base_url

    # Add path if provided
    if path:
        # Ensure path starts with / and base_url doesn't end with /
        if not path.startswith('/'):
            path = '/' + path
        if url.endswith('/'):
            url = url[:-1]

        url += path

    # Add query parameters if provided
    if params:
        # Filter out None values
        filtered_params = {k: v for k, v in params.items() if v is not None}

        if filtered_params:
            url += '?' + urlencode(filtered_params, doseq=True)

    return url
