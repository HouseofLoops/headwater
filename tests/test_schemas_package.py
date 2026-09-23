"""app.schemas must actually provide every name its __all__ advertises."""

import app.schemas


def test_every_exported_name_is_importable():
    missing = [name for name in app.schemas.__all__ if not hasattr(app.schemas, name)]
    assert missing == []


def test_package_exports_are_the_defining_objects():
    from app.schemas import OutputFormat, PaginatedResponse
    from app.schemas.enums import OutputFormat as EnumsOutputFormat
    from app.schemas.responses import PaginatedResponse as ResponsesPaginatedResponse

    assert OutputFormat is EnumsOutputFormat
    assert PaginatedResponse is ResponsesPaginatedResponse
