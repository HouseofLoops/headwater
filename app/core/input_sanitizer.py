"""
Input Sanitization and Validation Module.

This module provides comprehensive input sanitization, validation, and cleaning
for API parameters to prevent injection attacks and ensure data integrity.
"""

import html
import logging
import re
from typing import Any
from urllib.parse import quote

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class InputSanitizer:
    """
    Input sanitization and validation class with configurable rules.
    """

    def __init__(self):
        self.settings = get_settings()
        self._compile_patterns()

    def _compile_patterns(self):
        """Compile regex patterns for validation."""
        try:
            self.allowed_pattern = re.compile(self.settings.ALLOWED_CHARACTERS_PATTERN)
        except re.error as e:
            logger.warning("Invalid ALLOWED_CHARACTERS_PATTERN, using default: %s", str(e))
            self.allowed_pattern = re.compile(r"^[a-zA-Z0-9\s\-\.\,\?\!\(\)\[\]\{\}\'\"]+$")

        # Compile suspicious patterns
        self.suspicious_patterns = []
        for pattern in self.settings.SUSPICIOUS_PATTERNS:
            try:
                self.suspicious_patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as e:
                logger.warning("Invalid suspicious pattern '%s': %s", pattern, str(e))

    def sanitize_query(self, query: str) -> dict[str, Any]:
        """
        Sanitize and validate a search query.

        Args:
            query: The input query string

        Returns:
            Dict containing sanitized query and validation results
        """
        if not query:
            return {"sanitized": "", "valid": False, "errors": ["Query cannot be empty"], "warnings": []}

        # Basic cleaning
        cleaned = self._basic_clean(query)

        # Length validation
        if len(cleaned) > self.settings.MAX_QUERY_LENGTH:
            return {
                "sanitized": cleaned[: self.settings.MAX_QUERY_LENGTH],
                "valid": False,
                "errors": [f"Query too long (max {self.settings.MAX_QUERY_LENGTH} characters)"],
                "warnings": ["Query was truncated"],
            }

        # Pattern validation
        if not self.allowed_pattern.match(cleaned):
            return {
                "sanitized": self._remove_disallowed_chars(cleaned),
                "valid": False,
                "errors": ["Query contains invalid characters"],
                "warnings": ["Invalid characters were removed"],
            }

        # Suspicious pattern detection
        warnings = []
        if self.settings.BLOCK_SUSPICIOUS_PATTERNS:
            for pattern in self.suspicious_patterns:
                if pattern.search(cleaned):
                    warnings.append(f"Suspicious pattern detected: {pattern.pattern}")

        # URL encoding for safety
        sanitized = quote(cleaned, safe="")

        return {
            "sanitized": sanitized,
            "original": query,
            "valid": len(warnings) == 0,
            "errors": [],
            "warnings": warnings,
            "length": len(cleaned),
        }

    def sanitize_country_code(self, country_code: str) -> dict[str, Any]:
        """
        Sanitize and validate country code.

        Args:
            country_code: ISO country code

        Returns:
            Dict containing sanitized country code and validation results
        """
        if not country_code:
            return {
                "sanitized": "US",  # Default fallback
                "valid": True,
                "errors": [],
                "warnings": ["Empty country code, using default US"],
            }

        # Clean and validate
        cleaned = self._basic_clean(country_code.upper())

        # ISO country code pattern (2-3 letters)
        country_pattern = re.compile(r"^[A-Z]{2,3}$")
        if not country_pattern.match(cleaned):
            return {
                "sanitized": "US",
                "valid": False,
                "errors": ["Invalid country code format"],
                "warnings": ["Using default country code US"],
            }

        return {"sanitized": cleaned, "original": country_code, "valid": True, "errors": [], "warnings": []}

    def sanitize_language_code(self, language_code: str) -> dict[str, Any]:
        """
        Sanitize and validate language code.

        Args:
            language_code: ISO language code

        Returns:
            Dict containing sanitized language code and validation results
        """
        if not language_code:
            return {
                "sanitized": "en",  # Default fallback
                "valid": True,
                "errors": [],
                "warnings": ["Empty language code, using default en"],
            }

        # Clean and validate
        cleaned = self._basic_clean(language_code.lower())

        # ISO language code pattern (2-3 letters)
        lang_pattern = re.compile(r"^[a-z]{2,3}$")
        if not lang_pattern.match(cleaned):
            return {
                "sanitized": "en",
                "valid": False,
                "errors": ["Invalid language code format"],
                "warnings": ["Using default language code en"],
            }

        return {"sanitized": cleaned, "original": language_code, "valid": True, "errors": [], "warnings": []}

    def sanitize_integer_param(
        self, value: Any, param_name: str, min_val: int | None = None, max_val: int | None = None
    ) -> dict[str, Any]:
        """
        Sanitize and validate integer parameters.

        Args:
            value: The input value
            param_name: Name of the parameter for error messages
            min_val: Minimum allowed value
            max_val: Maximum allowed value

        Returns:
            Dict containing sanitized value and validation results
        """
        if value is None:
            return {"sanitized": None, "valid": True, "errors": [], "warnings": []}

        try:
            # Convert to int
            if isinstance(value, str):
                # Handle string representations
                cleaned = self._basic_clean(value)
                int_value = int(cleaned)
            else:
                int_value = int(value)

            # Range validation
            if min_val is not None and int_value < min_val:
                return {
                    "sanitized": min_val,
                    "valid": False,
                    "errors": [f"{param_name} too small (minimum {min_val})"],
                    "warnings": [f"Using minimum value {min_val}"],
                }

            if max_val is not None and int_value > max_val:
                return {
                    "sanitized": max_val,
                    "valid": False,
                    "errors": [f"{param_name} too large (maximum {max_val})"],
                    "warnings": [f"Using maximum value {max_val}"],
                }

            return {"sanitized": int_value, "original": value, "valid": True, "errors": [], "warnings": []}

        except ValueError, TypeError:
            return {"sanitized": None, "valid": False, "errors": [f"Invalid {param_name} format"], "warnings": []}

    def sanitize_string_param(self, value: Any, param_name: str, max_length: int | None = None) -> dict[str, Any]:
        """
        Sanitize and validate string parameters.

        Args:
            value: The input value
            param_name: Name of the parameter for error messages
            max_length: Maximum allowed length

        Returns:
            Dict containing sanitized value and validation results
        """
        if value is None:
            return {"sanitized": None, "valid": True, "errors": [], "warnings": []}

        try:
            # Convert to string and clean
            str_value = str(value)
            cleaned = self._basic_clean(str_value)

            # Length validation
            if max_length and len(cleaned) > max_length:
                return {
                    "sanitized": cleaned[:max_length],
                    "valid": False,
                    "errors": [f"{param_name} too long (maximum {max_length} characters)"],
                    "warnings": ["Parameter was truncated"],
                }

            # URL encoding for safety
            sanitized = quote(cleaned, safe="")

            return {"sanitized": sanitized, "original": value, "valid": True, "errors": [], "warnings": []}

        except ValueError, TypeError:
            return {"sanitized": None, "valid": False, "errors": [f"Invalid {param_name} format"], "warnings": []}

    def _basic_clean(self, text: str) -> str:
        """Basic text cleaning operations."""
        if not text:
            return ""

        # Convert to string if needed
        if not isinstance(text, str):
            text = str(text)

        # HTML entity decoding
        text = html.unescape(text)

        # Remove excessive whitespace
        text = re.sub(r"\s+", " ", text.strip())

        # Remove control characters
        text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)

        return text

    def _remove_disallowed_chars(self, text: str) -> str:
        """Remove characters that don't match the allowed pattern."""
        if not text:
            return ""

        # Keep only allowed characters
        allowed_chars = []
        for char in text:
            if re.match(r"[a-zA-Z0-9\s\-\.\,\?\!\(\)\[\]\{\}\'\"]", char):
                allowed_chars.append(char)

        return "".join(allowed_chars)

    def validate_all_params(self, **params) -> dict[str, Any]:
        """
        Validate all API parameters at once.

        Args:
            **params: All parameters to validate

        Returns:
            Dict containing validation results for all parameters
        """
        results = {}
        errors = []
        warnings = []

        # Validate query
        if "q" in params:
            query_result = self.sanitize_query(params["q"])
            results["query"] = query_result
            errors.extend(query_result["errors"])
            warnings.extend(query_result["warnings"])

        # Validate country code
        if "gl" in params:
            country_result = self.sanitize_country_code(params["gl"])
            results["country_code"] = country_result
            errors.extend(country_result["errors"])
            warnings.extend(country_result["warnings"])

        # Validate language code
        if "hl" in params:
            lang_result = self.sanitize_language_code(params["hl"])
            results["language_code"] = lang_result
            errors.extend(lang_result["errors"])
            warnings.extend(lang_result["warnings"])

        # Validate integer parameters
        int_params = {"spell": (0, 1), "cp": (0, None), "gs_rn": (0, None), "psi": (0, 1), "complete": (0, None)}

        for param, (min_val, max_val) in int_params.items():
            if param in params:
                int_result = self.sanitize_integer_param(params[param], param, min_val, max_val)
                results[param] = int_result
                errors.extend(int_result["errors"])
                warnings.extend(int_result["warnings"])

        # Validate string parameters
        str_params = ["cr", "ds", "gs_id", "callback", "jsonp", "pq", "suggid", "gs_l"]
        for param in str_params:
            if param in params:
                str_result = self.sanitize_string_param(params[param], param, 100)
                results[param] = str_result
                errors.extend(str_result["errors"])
                warnings.extend(str_result["warnings"])

        return {
            "valid": len(errors) == 0,
            "results": results,
            "errors": errors,
            "warnings": warnings,
            "total_params": len(params),
        }


# Global sanitizer instance
_sanitizer: InputSanitizer | None = None


def get_input_sanitizer() -> InputSanitizer:
    """Get the global input sanitizer instance."""
    if _sanitizer is None:
        return InputSanitizer()
    return _sanitizer


def set_input_sanitizer(sanitizer: InputSanitizer):
    """Set the global input sanitizer instance (for testing)."""
    # Use object.__setattr__ to avoid global statement
    globals()["_sanitizer"] = sanitizer


def sanitize_input(**params) -> dict[str, Any]:
    """
    Convenience function to sanitize input parameters.

    Args:
        **params: Parameters to sanitize

    Returns:
        Dict containing sanitized parameters and validation results
    """
    sanitizer = get_input_sanitizer()
    return sanitizer.validate_all_params(**params)
