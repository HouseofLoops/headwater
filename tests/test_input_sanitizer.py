from unittest.mock import MagicMock, patch


from app.core.input_sanitizer import InputSanitizer, get_input_sanitizer, sanitize_input, set_input_sanitizer


class TestInputSanitizer:
    """Test cases for InputSanitizer class."""

    def setup_method(self):
        """Clear the global sanitizer before each test."""
        set_input_sanitizer(None)

    def teardown_method(self):
        """Clear the global sanitizer after each test."""
        set_input_sanitizer(None)

    def test_init(self):
        """Test InputSanitizer initialization."""
        sanitizer = InputSanitizer()
        assert sanitizer.settings is not None
        assert hasattr(sanitizer, "allowed_pattern")
        assert hasattr(sanitizer, "suspicious_patterns")

    def test_compile_patterns_default(self):
        """Test pattern compilation with default settings."""
        sanitizer = InputSanitizer()
        # Should compile default patterns when settings are available
        assert sanitizer.allowed_pattern is not None
        assert len(sanitizer.suspicious_patterns) > 0

    def test_compile_patterns_custom(self):
        """Test pattern compilation with custom settings."""
        with patch("app.core.input_sanitizer.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.ALLOWED_CHARACTERS_PATTERN = r"^[a-z]+$"
            mock_settings.SUSPICIOUS_PATTERNS = [r"test.*pattern"]
            mock_get_settings.return_value = mock_settings

            sanitizer = InputSanitizer()
            assert sanitizer.allowed_pattern.pattern == r"^[a-z]+$"
            assert len(sanitizer.suspicious_patterns) == 1

    def test_compile_patterns_invalid(self):
        """Test pattern compilation with invalid patterns."""
        with patch("app.core.input_sanitizer.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.ALLOWED_CHARACTERS_PATTERN = r"[invalid"
            mock_settings.SUSPICIOUS_PATTERNS = [r"[also", r"valid"]
            mock_get_settings.return_value = mock_settings

            sanitizer = InputSanitizer()
            # Should fall back to default pattern
            assert sanitizer.allowed_pattern.pattern == r"^[a-zA-Z0-9\s\-\.\,\?\!\(\)\[\]\{\}\'\"]+$"
            assert len(sanitizer.suspicious_patterns) == 1  # Only valid pattern

    def test_sanitize_query_empty(self):
        """Test sanitizing empty query."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_query("")

        assert result["valid"] is False
        assert result["sanitized"] == ""
        assert "Query cannot be empty" in result["errors"]

    def test_sanitize_query_valid(self):
        """Test sanitizing valid query."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_query("python tutorial")

        assert result["valid"] is True
        assert result["sanitized"] == "python%20tutorial"
        assert result["original"] == "python tutorial"
        assert len(result["errors"]) == 0
        assert len(result["warnings"]) == 0

    def test_sanitize_query_too_long(self):
        """Test sanitizing query that's too long."""
        with patch("app.core.input_sanitizer.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.MAX_QUERY_LENGTH = 10
            mock_settings.ALLOWED_CHARACTERS_PATTERN = r"^[a-zA-Z0-9\s\-\.\,\?\!\(\)\[\]\{\}\'\"]+$"
            mock_settings.SUSPICIOUS_PATTERNS = []
            mock_get_settings.return_value = mock_settings

            sanitizer = InputSanitizer()
            result = sanitizer.sanitize_query("this is a very long query")

            assert result["valid"] is False
            assert len(result["sanitized"]) == 10
            assert "Query too long" in result["errors"][0]
            assert "Query was truncated" in result["warnings"][0]

    def test_sanitize_query_invalid_chars(self):
        """Test sanitizing query with invalid characters."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_query("python<script>alert('xss')</script>")

        assert result["valid"] is False
        # The implementation removes invalid characters (< >), but keeps valid ones
        assert "<" not in result["sanitized"]
        assert ">" not in result["sanitized"]
        # Parentheses are allowed characters, so they remain
        assert "pythonscriptalert('xss')script" in result["sanitized"]
        assert "Query contains invalid characters" in result["errors"][0]
        assert "Invalid characters were removed" in result["warnings"][0]

    def test_sanitize_query_suspicious_pattern(self):
        """Test sanitizing query with suspicious pattern."""
        with patch("app.core.input_sanitizer.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.MAX_QUERY_LENGTH = 1000
            mock_settings.ALLOWED_CHARACTERS_PATTERN = r"^[a-zA-Z0-9\s\-\.\,\?\!\(\)\[\]\{\}\'\"]+$"
            mock_settings.SUSPICIOUS_PATTERNS = [r"<script"]
            mock_settings.BLOCK_SUSPICIOUS_PATTERNS = True
            mock_get_settings.return_value = mock_settings

            sanitizer = InputSanitizer()
            result = sanitizer.sanitize_query("python<script>")

            assert result["valid"] is False
            # Should detect suspicious pattern and mark as invalid
            assert len(result["warnings"]) > 0

    def test_sanitize_country_code_empty(self):
        """Test sanitizing empty country code."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_country_code("")

        assert result["valid"] is True
        assert result["sanitized"] == "US"
        assert "Empty country code, using default US" in result["warnings"][0]

    def test_sanitize_country_code_valid(self):
        """Test sanitizing valid country code."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_country_code("gb")

        assert result["valid"] is True
        assert result["sanitized"] == "GB"
        assert result["original"] == "gb"

    def test_sanitize_country_code_invalid(self):
        """Test sanitizing invalid country code."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_country_code("invalid")

        assert result["valid"] is False
        assert result["sanitized"] == "US"
        assert "Invalid country code format" in result["errors"][0]

    def test_sanitize_language_code_empty(self):
        """Test sanitizing empty language code."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_language_code("")

        assert result["valid"] is True
        assert result["sanitized"] == "en"
        assert "Empty language code, using default en" in result["warnings"][0]

    def test_sanitize_language_code_valid(self):
        """Test sanitizing valid language code."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_language_code("es")

        assert result["valid"] is True
        assert result["sanitized"] == "es"
        assert result["original"] == "es"

    def test_sanitize_language_code_invalid(self):
        """Test sanitizing invalid language code."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_language_code("invalid")

        assert result["valid"] is False
        assert result["sanitized"] == "en"
        assert "Invalid language code format" in result["errors"][0]

    def test_sanitize_integer_param_none(self):
        """Test sanitizing None integer parameter."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_integer_param(None, "test_param")

        assert result["valid"] is True
        assert result["sanitized"] is None

    def test_sanitize_integer_param_valid(self):
        """Test sanitizing valid integer parameter."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_integer_param(42, "test_param")

        assert result["valid"] is True
        assert result["sanitized"] == 42
        assert result["original"] == 42

    def test_sanitize_integer_param_string(self):
        """Test sanitizing string integer parameter."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_integer_param("42", "test_param")

        assert result["valid"] is True
        assert result["sanitized"] == 42
        assert result["original"] == "42"

    def test_sanitize_integer_param_invalid(self):
        """Test sanitizing invalid integer parameter."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_integer_param("not_a_number", "test_param")

        assert result["valid"] is False
        assert result["sanitized"] is None
        assert "Invalid test_param format" in result["errors"][0]

    def test_sanitize_integer_param_range(self):
        """Test sanitizing integer parameter with range validation."""
        sanitizer = InputSanitizer()

        # Test minimum
        result = sanitizer.sanitize_integer_param(5, "test_param", min_val=10)
        assert result["valid"] is False
        assert result["sanitized"] == 10
        assert "too small" in result["errors"][0]

        # Test maximum
        result = sanitizer.sanitize_integer_param(15, "test_param", max_val=10)
        assert result["valid"] is False
        assert result["sanitized"] == 10
        assert "too large" in result["errors"][0]

    def test_sanitize_string_param_none(self):
        """Test sanitizing None string parameter."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_string_param(None, "test_param")

        assert result["valid"] is True
        assert result["sanitized"] is None

    def test_sanitize_string_param_valid(self):
        """Test sanitizing valid string parameter."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_string_param("test value", "test_param")

        assert result["valid"] is True
        assert result["sanitized"] == "test%20value"
        assert result["original"] == "test value"

    def test_sanitize_string_param_too_long(self):
        """Test sanitizing string parameter that's too long."""
        sanitizer = InputSanitizer()
        result = sanitizer.sanitize_string_param("very long string", "test_param", max_length=10)

        assert result["valid"] is False
        # Should truncate but not URL encode when truncated
        assert result["sanitized"] == "very long "
        assert "too long" in result["errors"][0]
        assert "truncated" in result["warnings"][0]

    def test_basic_clean(self):
        """Test basic cleaning functionality through public methods."""
        sanitizer = InputSanitizer()
        # Test HTML entity decoding through sanitize_query
        result = sanitizer.sanitize_query("&lt;script&gt;")
        # HTML entities get decoded, but < > are invalid chars and get removed
        assert result["sanitized"] == "script"

        # Test whitespace normalization
        result = sanitizer.sanitize_query("  multiple   spaces  ")
        assert result["sanitized"] == "multiple%20spaces"

        # Test control character removal
        result = sanitizer.sanitize_query("text\x00\x01control")
        assert result["sanitized"] == "textcontrol"

    def test_remove_disallowed_chars(self):
        """Test disallowed character removal through public methods."""
        sanitizer = InputSanitizer()
        # Test through sanitize_query with invalid characters
        result = sanitizer.sanitize_query("Hello@World#123")
        # Should remove @ and # characters
        assert "@" not in result["sanitized"]
        assert "#" not in result["sanitized"]
        assert result["valid"] is False  # Because invalid chars were removed

    def test_validate_all_params_empty(self):
        """Test validating empty parameters."""
        sanitizer = InputSanitizer()
        result = sanitizer.validate_all_params()

        assert result["valid"] is True
        assert len(result["results"]) == 0
        assert len(result["errors"]) == 0
        assert len(result["warnings"]) == 0

    def test_validate_all_params_valid(self):
        """Test validating valid parameters."""
        sanitizer = InputSanitizer()
        result = sanitizer.validate_all_params(q="python tutorial", gl="US", hl="en", spell=1, cr="countryUS")

        assert result["valid"] is True
        assert "query" in result["results"]
        assert "country_code" in result["results"]
        assert "language_code" in result["results"]
        assert "spell" in result["results"]
        assert len(result["errors"]) == 0

    def test_validate_all_params_invalid(self):
        """Test validating invalid parameters."""
        sanitizer = InputSanitizer()
        result = sanitizer.validate_all_params(
            q="",  # Empty query
            gl="INVALID",  # Invalid country
            spell="not_a_number",  # Invalid integer
        )

        assert result["valid"] is False
        assert len(result["errors"]) > 0
        assert len(result["warnings"]) > 0

    def test_get_input_sanitizer(self):
        """Test getting the global input sanitizer instance."""
        # Clear any existing instance
        set_input_sanitizer(None)

        # First call should create new instance
        sanitizer1 = get_input_sanitizer()
        assert isinstance(sanitizer1, InputSanitizer)

        # Set the global instance manually for testing
        set_input_sanitizer(sanitizer1)

        # Second call should return same instance
        sanitizer2 = get_input_sanitizer()
        assert sanitizer1 is sanitizer2

    def test_set_input_sanitizer(self):
        """Test setting the global input sanitizer instance."""
        # Clear any existing instance
        set_input_sanitizer(None)

        # Create and set custom sanitizer
        custom_sanitizer = MagicMock()
        set_input_sanitizer(custom_sanitizer)

        # Should return the custom instance
        assert get_input_sanitizer() is custom_sanitizer

    def test_sanitize_input_function(self):
        """Test the sanitize_input convenience function."""
        with patch("app.core.input_sanitizer.get_input_sanitizer") as mock_get:
            mock_sanitizer = MagicMock()
            mock_sanitizer.validate_all_params.return_value = {"test": "result"}
            mock_get.return_value = mock_sanitizer

            result = sanitize_input(q="test query")
            assert result == {"test": "result"}
            mock_sanitizer.validate_all_params.assert_called_once_with(q="test query")
