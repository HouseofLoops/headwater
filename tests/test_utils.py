"""
Test suite for utils.py module.

This module contains comprehensive tests for all utility functions
in the utils.py module, covering datetime formatting, JSON handling,
string manipulation, enum operations, and more.
"""

import datetime
import json
import tempfile
import uuid
from enum import Enum
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.utils import (
    batch_process,
    build_url,
    camel_to_snake,
    # List utilities
    chunks,
    deep_get,
    deep_set,
    extract_emails,
    extract_hashtags,
    extract_mentions,
    # Text extraction utilities
    extract_urls,
    find_modules,
    flatten_dict,
    # DateTime utilities
    format_datetime,
    from_dict,
    from_json,
    # String utilities
    generate_uuid,
    get_class_methods,
    get_enum_dict,
    get_enum_names,
    # Enum utilities
    get_enum_values,
    # File utilities
    get_file_extension,
    get_file_size_str,
    # Function inspection utilities
    get_function_args,
    get_function_defaults,
    get_mime_type,
    get_subclasses,
    # Module utilities
    import_string,
    is_audio_file,
    is_email,
    is_image_file,
    is_phone_number,
    is_url,
    # Validation utilities
    is_valid_json,
    is_video_file,
    memoize,
    # Dictionary utilities
    merge_dicts,
    parse_datetime,
    # URL utilities
    parse_query_params,
    # Decorators
    safe_json_loads,
    slugify,
    snake_to_camel,
    snake_to_pascal,
    timeit,
    to_dict,
    # JSON utilities
    to_json,
    truncate_string,
    unflatten_dict,
)


class TestDateTimeUtils:
    """Test datetime utility functions."""

    def test_format_datetime_default(self):
        """Test formatting current datetime with default format."""
        result = format_datetime()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_datetime_custom(self):
        """Test formatting datetime with custom format."""
        dt = datetime.datetime(2023, 12, 25, 15, 30, 45)
        result = format_datetime(dt, "%Y-%m-%d")
        assert result == "2023-12-25"

    def test_parse_datetime_valid(self):
        """Test parsing valid datetime string."""
        dt_str = "2023-12-25 15:30:45"
        result = parse_datetime(dt_str)
        assert isinstance(result, datetime.datetime)
        assert result.year == 2023
        assert result.month == 12
        assert result.day == 25

    def test_parse_datetime_invalid(self):
        """Test parsing invalid datetime string."""
        with pytest.raises(ValueError):
            parse_datetime("invalid-date")


class TestJSONUtils:
    """Test JSON utility functions."""

    def test_to_json_basic(self):
        """Test basic JSON conversion."""
        data = {"key": "value", "number": 42}
        result = to_json(data)
        assert isinstance(result, str)
        assert json.loads(result) == data

    def test_to_json_with_options(self):
        """Test JSON conversion with options."""
        data = {"key": "value", "none_val": None}
        result = to_json(data, exclude_none=True, indent=2)
        parsed = json.loads(result)
        assert "none_val" not in parsed

    def test_to_dict_basic(self):
        """Test basic dictionary conversion."""
        data = {"key": "value"}
        result = to_dict(data)
        assert isinstance(result, dict)
        assert result == data

    def test_from_dict_basic(self):
        """Test basic model creation from dict."""
        class TestModel:
            def __init__(self, key: str):
                self.key = key

        data = {"key": "value"}
        result = from_dict(data, TestModel)
        assert isinstance(result, TestModel)
        assert result.key == "value"

    def test_from_json_basic(self):
        """Test basic model creation from JSON."""
        class TestModel:
            def __init__(self, key: str):
                self.key = key

        json_str = '{"key": "value"}'
        result = from_json(json_str, TestModel)
        assert isinstance(result, TestModel)
        assert result.key == "value"

    def test_from_json_invalid(self):
        """Test invalid JSON handling."""
        class TestModel:
            def __init__(self, key: str):
                self.key = key

        with pytest.raises(json.JSONDecodeError):
            from_json("invalid json", TestModel)


class TestStringUtils:
    """Test string utility functions."""

    def test_generate_uuid(self):
        """Test UUID generation."""
        result = generate_uuid()
        assert isinstance(result, str)
        # Should be valid UUID format
        uuid.UUID(result)

    def test_slugify_basic(self):
        """Test basic slugification."""
        result = slugify("Hello World!")
        assert result == "hello-world"

    def test_slugify_complex(self):
        """Test complex slugification."""
        result = slugify("Hello, World! How are you?")
        assert result == "hello-world-how-are-you"

    def test_slugify_empty(self):
        """Test slugification of empty string."""
        result = slugify("")
        assert result == ""

    def test_truncate_string_short(self):
        """Test truncating short string."""
        result = truncate_string("short", 10)
        assert result == "short"

    def test_truncate_string_long(self):
        """Test truncating long string."""
        result = truncate_string("very long string", 10)
        assert result == "very lo..."

    def test_truncate_string_custom_suffix(self):
        """Test truncating with custom suffix."""
        result = truncate_string("very long string", 10, "[...]")
        assert result == "very [...]"

    def test_camel_to_snake_basic(self):
        """Test basic camelCase to snake_case conversion."""
        result = camel_to_snake("camelCase")
        assert result == "camel_case"

    def test_camel_to_snake_complex(self):
        """Test complex camelCase to snake_case conversion."""
        result = camel_to_snake("XMLHttpRequest")
        assert result == "xml_http_request"

    def test_snake_to_camel_basic(self):
        """Test basic snake_case to camelCase conversion."""
        result = snake_to_camel("snake_case")
        assert result == "snakeCase"

    def test_snake_to_pascal_basic(self):
        """Test basic snake_case to PascalCase conversion."""
        result = snake_to_pascal("snake_case")
        assert result == "SnakeCase"


class TestEnumUtils:
    """Test enum utility functions."""

    class TestEnum(Enum):
        VALUE1 = "value1"
        VALUE2 = "value2"
        VALUE3 = 42

    def test_get_enum_values(self):
        """Test getting enum values."""
        result = get_enum_values(self.TestEnum)
        assert isinstance(result, list)
        assert "value1" in result
        assert "value2" in result
        assert 42 in result

    def test_get_enum_names(self):
        """Test getting enum names."""
        result = get_enum_names(self.TestEnum)
        assert isinstance(result, list)
        assert "VALUE1" in result
        assert "VALUE2" in result
        assert "VALUE3" in result

    def test_get_enum_dict(self):
        """Test getting enum as dictionary."""
        result = get_enum_dict(self.TestEnum)
        assert isinstance(result, dict)
        assert result["VALUE1"] == "value1"
        assert result["VALUE2"] == "value2"
        assert result["VALUE3"] == 42

    def test_get_enum_invalid_type(self):
        """Test enum utilities with invalid type."""
        with pytest.raises(TypeError):
            get_enum_values(str)


class TestInspectionUtils:
    """Test function and class inspection utilities."""

    def test_get_function_args(self):
        """Test getting function arguments."""
        def test_func(arg1: str, arg2: int = 42) -> str:
            return f"{arg1}-{arg2}"

        result = get_function_args(test_func)
        assert isinstance(result, list)
        assert "arg1" in result
        assert "arg2" in result

    def test_get_function_defaults(self):
        """Test getting function defaults."""
        def test_func(arg1: str, arg2: int = 42) -> str:
            return f"{arg1}-{arg2}"

        result = get_function_defaults(test_func)
        assert isinstance(result, dict)
        assert result["arg2"] == 42

    def test_get_class_methods(self):
        """Test getting class methods."""
        class TestClass:
            def method1(self):  # Test method 1
                pass

            def method2(self):  # Test method 2
                pass

            def _private_method(self):  # Private test method
                pass

        result = get_class_methods(TestClass)
        assert isinstance(result, list)
        assert "method1" in result
        assert "method2" in result
        assert "_private_method" not in result

    def test_get_subclasses(self):
        """Test getting subclasses."""
        class BaseClass: pass
        class SubClass1(BaseClass): pass
        class SubClass2(BaseClass): pass
        class SubSubClass(SubClass1): pass

        result = get_subclasses(BaseClass)
        assert isinstance(result, list)
        assert SubClass1 in result
        assert SubClass2 in result
        assert SubSubClass in result


class TestModuleUtils:
    """Test module utility functions."""

    def test_import_string_valid(self):
        """Test importing valid module string."""
        result = import_string("json.dumps")
        assert result == json.dumps

    def test_import_string_invalid_module(self):
        """Test importing invalid module."""
        with pytest.raises(ImportError):
            import_string("nonexistent.module")

    def test_import_string_invalid_attribute(self):
        """Test importing invalid attribute."""
        with pytest.raises(ImportError):
            import_string("json.nonexistent")

    def test_find_modules_basic(self):
        """Test finding modules in directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test files
            Path(temp_dir, "module1.py").touch()
            Path(temp_dir, "module2.py").touch()
            Path(temp_dir, "__init__.py").touch()

            result = find_modules(temp_dir)
            assert isinstance(result, list)
            assert "module1" in result
            assert "module2" in result


class TestDictUtils:
    """Test dictionary utility functions."""

    def test_merge_dicts_basic(self):
        """Test basic dictionary merging."""
        dict1 = {"a": 1, "b": 2}
        dict2 = {"b": 3, "c": 4}
        result = merge_dicts(dict1, dict2)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_merge_dicts_nested(self):
        """Test nested dictionary merging."""
        dict1 = {"nested": {"a": 1}}
        dict2 = {"nested": {"b": 2}}
        result = merge_dicts(dict1, dict2)
        assert result == {"nested": {"a": 1, "b": 2}}

    def test_flatten_dict_basic(self):
        """Test basic dictionary flattening."""
        data = {"a": {"b": {"c": 1}}}
        result = flatten_dict(data)
        assert result == {"a.b.c": 1}

    def test_unflatten_dict_basic(self):
        """Test basic dictionary unflattening."""
        data = {"a.b.c": 1}
        result = unflatten_dict(data)
        assert result == {"a": {"b": {"c": 1}}}

    def test_deep_get_basic(self):
        """Test basic deep get."""
        data = {"a": {"b": {"c": 1}}}
        result = deep_get(data, "a.b.c")
        assert result == 1

    def test_deep_get_default(self):
        """Test deep get with default."""
        data = {"a": {"b": {}}}
        result = deep_get(data, "a.b.c", default="not found")
        assert result == "not found"

    def test_deep_set_basic(self):
        """Test basic deep set."""
        data = {}
        result = deep_set(data, "a.b.c", 1)
        assert result == {"a": {"b": {"c": 1}}}


class TestListUtils:
    """Test list utility functions."""

    def test_chunks_basic(self):
        """Test basic list chunking."""
        data = [1, 2, 3, 4, 5]
        result = chunks(data, 2)
        assert result == [[1, 2], [3, 4], [5]]

    def test_chunks_empty(self):
        """Test chunking empty list."""
        result = chunks([], 2)
        assert result == []

    def test_batch_process_basic(self):
        """Test basic batch processing."""
        def process_func(batch):
            return [x * 2 for x in batch]

        data = [1, 2, 3, 4, 5]
        result = batch_process(data, process_func, batch_size=2)
        assert result == [2, 4, 6, 8, 10]


class TestDecorators:
    """Test decorator functions."""

    def test_memoize_basic(self):
        """Test basic memoization."""
        call_count = 0

        @memoize
        def test_func(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        # First call
        result1 = test_func(5)
        assert result1 == 10
        assert call_count == 1

        # Second call with same argument should use cache
        result2 = test_func(5)
        assert result2 == 10
        assert call_count == 1  # Should not increment

    def test_timeit_basic(self):
        """Test basic timing decorator."""
        @timeit
        def test_func():
            return "result"

        with patch('app.core.decorators.logger') as mock_logger:
            result = test_func()
            assert result == "result"
            mock_logger.debug.assert_called_once()


class TestURLUtils:
    """Test URL utility functions."""

    def test_parse_query_params_basic(self):
        """Test basic query parameter parsing."""
        url = "https://example.com?a=1&b=2"
        result = parse_query_params(url)
        assert result == {"a": ["1"], "b": ["2"]}

    def test_build_url_basic(self):
        """Test basic URL building."""
        result = build_url("https://example.com", "/api", {"a": 1, "b": 2})
        assert result == "https://example.com/api?a=1&b=2"

    def test_build_url_no_params(self):
        """Test URL building without parameters."""
        result = build_url("https://example.com", "/api")
        assert result == "https://example.com/api"


class TestValidationUtils:
    """Test validation utility functions."""

    def test_is_valid_json_valid(self):
        """Test valid JSON detection."""
        result = is_valid_json('{"key": "value"}')
        assert result is True

    def test_is_valid_json_invalid(self):
        """Test invalid JSON detection."""
        result = is_valid_json("invalid json")
        assert result is False

    def test_safe_json_loads_valid(self):
        """Test safe JSON loading with valid JSON."""
        result = safe_json_loads('{"key": "value"}')
        assert result == {"key": "value"}

    def test_safe_json_loads_invalid(self):
        """Test safe JSON loading with invalid JSON."""
        result = safe_json_loads("invalid json", default="fallback")
        assert result == "fallback"

    def test_is_url_valid(self):
        """Test valid URL detection."""
        result = is_url("https://example.com")
        assert result is True

    def test_is_url_invalid(self):
        """Test invalid URL detection."""
        result = is_url("not a url")
        assert result is False

    def test_is_email_valid(self):
        """Test valid email detection."""
        result = is_email("test@example.com")
        assert result is True

    def test_is_email_invalid(self):
        """Test invalid email detection."""
        result = is_email("not an email")
        assert result is False

    def test_is_phone_number_valid(self):
        """Test valid phone number detection."""
        result = is_phone_number("+1234567890")
        assert result is True

    def test_is_phone_number_invalid(self):
        """Test invalid phone number detection."""
        result = is_phone_number("not a phone")
        assert result is False


class TestFileUtils:
    """Test file utility functions."""

    def test_get_file_extension_basic(self):
        """Test basic file extension extraction."""
        result = get_file_extension("test.jpg")
        assert result == ".jpg"

    def test_get_file_extension_no_ext(self):
        """Test file extension extraction for files without extension."""
        result = get_file_extension("test")
        assert result == ""

    def test_is_image_file_valid(self):
        """Test valid image file detection."""
        result = is_image_file("test.jpg")
        assert result is True

    def test_is_image_file_invalid(self):
        """Test invalid image file detection."""
        result = is_image_file("test.txt")
        assert result is False

    def test_is_video_file_valid(self):
        """Test valid video file detection."""
        result = is_video_file("test.mp4")
        assert result is True

    def test_is_audio_file_valid(self):
        """Test valid audio file detection."""
        result = is_audio_file("test.mp3")
        assert result is True

    def test_get_file_size_str_bytes(self):
        """Test file size string for bytes."""
        result = get_file_size_str(512)
        assert result == "512 B"

    def test_get_file_size_str_kb(self):
        """Test file size string for kilobytes."""
        result = get_file_size_str(1536)
        assert "KB" in result

    def test_get_file_size_str_mb(self):
        """Test file size string for megabytes."""
        result = get_file_size_str(1048576)
        assert "MB" in result

    def test_get_mime_type_basic(self):
        """Test basic MIME type detection."""
        result = get_mime_type("test.jpg")
        assert result == "image/jpeg"


class TestTextExtractionUtils:
    """Test text extraction utility functions."""

    def test_extract_urls_basic(self):
        """Test basic URL extraction."""
        text = "Visit https://example.com and http://test.com"
        result = extract_urls(text)
        assert "https://example.com" in result
        assert "http://test.com" in result

    def test_extract_emails_basic(self):
        """Test basic email extraction."""
        text = "Contact test@example.com or user@test.com"
        result = extract_emails(text)
        assert "test@example.com" in result
        assert "user@test.com" in result

    def test_extract_hashtags_basic(self):
        """Test basic hashtag extraction."""
        text = "Check out #python and #programming"
        result = extract_hashtags(text)
        assert "#python" in result
        assert "#programming" in result

    def test_extract_mentions_basic(self):
        """Test basic mention extraction."""
        text = "Hello @user1 and @user2"
        result = extract_mentions(text)
        assert "@user1" in result
        assert "@user2" in result
