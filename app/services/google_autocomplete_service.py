"""
Google Autocomplete Service.

This module handles all business logic for fetching and processing
Google Autocomplete suggestions, including keyword variation generation.
"""
import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from app.core.constants import KEYWORD_CATEGORIES

logger = logging.getLogger(__name__)


class GoogleAutocompleteService:
    """Service class for Google Autocomplete operations."""

    GOOGLE_AUTOCOMPLETE_URL = "https://www.google.com/complete/search"

    def __init__(self):
        """Initialize the service."""
        self.categories = KEYWORD_CATEGORIES

    def build_request_params(
        self,
        query: str,
        output: str = "toolbar",
        gl: str = "US",
        hl: str = "en",
        client: str | None = None,
        ds: str | None = None,
        spell: int | None = None,
        **kwargs
    ) -> dict[str, Any]:
        """
        Build request parameters for Google Autocomplete API.

        Args:
            query: Search query
            output: Response format
            gl: Geographic location
            hl: Host language
            client: Client identifier
            ds: Data source
            spell: Spell correction flag
            **kwargs: Additional parameters

        Returns:
            Dictionary of request parameters
        """
        params = {
            "q": query,
            "output": output,
            "gl": gl,
            "hl": hl,
        }

        if client:
            params["client"] = client
        if ds:
            params["ds"] = ds
        if spell is not None:
            params["spell"] = spell

        # Add any additional parameters
        for key, value in kwargs.items():
            if value is not None:
                params[key] = value

        return params

    def parse_json_response(self, data: list) -> dict[str, Any]:
        """
        Parse Google Autocomplete JSON response.

        Args:
            data: Raw JSON response data

        Returns:
            Parsed response dictionary
        """
        return {
            "original_query": data[0] if len(data) > 0 else "",
            "suggestions": data[1] if len(data) > 1 else [],
            "descriptions": data[2] if len(data) > 2 else [],
            "query_completions": data[3] if len(data) > 3 else [],
            "metadata": data[4] if len(data) > 4 else {},
        }

    def parse_xml_response(self, content: bytes) -> list[str]:
        """
        Parse Google Autocomplete XML response.

        Args:
            content: Raw XML response content

        Returns:
            List of suggestion strings
        """
        suggestions = []
        try:
            root = ET.fromstring(content)
            for complete_suggestion in root.findall("CompleteSuggestion"):
                suggestion_element = complete_suggestion.find("suggestion")
                if suggestion_element is not None:
                    data = suggestion_element.get("data", "")
                    suggestions.append(data)
        except ET.ParseError as e:
            logger.error(f"XML Parse Error: {e!s}")
        return suggestions

    def extract_suggestions_from_response(
        self,
        response_text: str,
        output_format: str,
        client: str | None = None
    ) -> dict[str, Any]:
        """
        Extract suggestions from API response based on format.

        Args:
            response_text: Raw response text
            output_format: Expected output format
            client: Client identifier (affects response format)

        Returns:
            Dictionary with suggestions and metadata
        """
        result = {
            "suggestions": [],
            "metadata": {},
            "response_type": "unknown"
        }

        # Determine if we should try JSON first
        should_try_json = (
            client is not None or
            output_format.lower() in ["chrome", "firefox", "safari", "opera"]
        )

        response_text = response_text.strip()
        looks_like_json = response_text.startswith(('[', '{'))
        looks_like_jsonp = "(" in response_text and response_text.endswith(")")

        if should_try_json or looks_like_json or looks_like_jsonp:
            try:
                if looks_like_jsonp:
                    # Extract JSON from JSONP wrapper
                    start_idx = response_text.find('(')
                    end_idx = response_text.rfind(')')
                    if start_idx != -1 and end_idx != -1:
                        json_str = response_text[start_idx + 1:end_idx]
                        data = json.loads(json_str)
                        result["response_type"] = "jsonp"
                else:
                    data = json.loads(response_text)
                    result["response_type"] = "json"

                if isinstance(data, list):
                    parsed = self.parse_json_response(data)
                    result["suggestions"] = parsed["suggestions"]
                    result["metadata"] = parsed["metadata"]

            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"JSON parsing failed: {e}")

        # Fall back to XML if needed
        if not result["suggestions"] and output_format.lower() in ["xml", "toolbar"]:
            result["suggestions"] = self.parse_xml_response(response_text.encode())
            result["response_type"] = "xml"

        return result

    def build_modified_query(self, category: str, prefix: str, base_query: str) -> str:
        """
        Build modified query for keyword variation.

        Args:
            category: Category name
            prefix: Prefix to add
            base_query: Base query string

        Returns:
            Modified query string
        """
        if category.startswith("Intent-Based"):
            return f"{prefix} {base_query}"
        elif category == "Alphabet":
            return f"{base_query} {prefix}"
        else:
            return f"{prefix} {base_query}"

    async def fetch_suggestions_async(
        self,
        http_client: httpx.AsyncClient,
        query: str,
        params: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Fetch suggestions asynchronously.

        Args:
            http_client: Async HTTP client
            query: Search query
            params: Request parameters

        Returns:
            Dictionary with suggestions and metadata
        """
        result = {
            "suggestions": [],
            "original_query": query,
            "metadata": {},
            "response_type": "unknown"
        }

        try:
            response = await http_client.get(self.GOOGLE_AUTOCOMPLETE_URL, params=params)

            if response.status_code != 200:
                logger.error(f"Failed to fetch suggestions for '{query}': {response.status_code}")
                return result

            extracted = self.extract_suggestions_from_response(
                response.text,
                params.get("output", "toolbar"),
                params.get("client")
            )

            result.update(extracted)

            # Normalize suggestions to lowercase
            result["suggestions"] = [s.lower() for s in result["suggestions"]]

        except Exception as e:
            logger.error(f"Error fetching suggestions for '{query}': {e}")

        return result

    async def generate_keyword_variations_parallel(
        self,
        http_client: httpx.AsyncClient,
        base_query: str,
        params: dict[str, Any],
        max_parallel: int = 10
    ) -> dict[str, Any]:
        """
        Generate keyword variations using parallel processing.

        Args:
            http_client: Async HTTP client
            base_query: Base query string
            params: Base request parameters
            max_parallel: Maximum parallel requests

        Returns:
            Dictionary with categorized suggestions and metadata
        """
        categorized_suggestions = {key: {} for key in self.categories.keys()}
        metadata_collection = {}

        # Build list of all tasks
        tasks = []
        task_info = []

        for category, prefixes in self.categories.items():
            for prefix in prefixes:
                modified_query = self.build_modified_query(category, prefix, base_query)
                task_params = params.copy()
                task_params["q"] = modified_query

                task = self.fetch_suggestions_async(http_client, modified_query, task_params)
                tasks.append(task)
                task_info.append({
                    "category": category,
                    "prefix": prefix,
                    "query": modified_query
                })

        logger.info(f"Starting parallel processing of {len(tasks)} variation queries")

        # Process in batches
        for i in range(0, len(tasks), max_parallel):
            batch_tasks = tasks[i:i + max_parallel]
            batch_info = task_info[i:i + max_parallel]

            try:
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

                for j, result in enumerate(batch_results):
                    info = batch_info[j]
                    if isinstance(result, Exception):
                        logger.warning(f"Error for '{info['query']}': {result}")
                        categorized_suggestions[info["category"]][info["prefix"]] = []
                    else:
                        categorized_suggestions[info["category"]][info["prefix"]] = result["suggestions"]
                        if result.get("metadata"):
                            query_key = f"{info['category']}:{info['prefix']}"
                            metadata_collection[query_key] = {
                                "query": info["query"],
                                "metadata": result["metadata"],
                                "response_type": result["response_type"]
                            }
            except Exception as e:
                logger.error(f"Error processing batch: {e}")

        logger.info(f"Completed parallel processing of {len(tasks)} variation queries")

        return {
            "suggestions": categorized_suggestions,
            "metadata": metadata_collection
        }


# Singleton instance for convenience
google_autocomplete_service = GoogleAutocompleteService()
