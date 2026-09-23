"""
Utility functions for the Headwater application.

This module provides shared helper functions for common tasks like
datetime formatting, JSON serialization, and other utilities.

The helpers now live in topic modules (datetime_utils, serialization_utils,
text_utils, introspection_utils, collection_utils, decorators, url_utils,
file_utils); this module re-exports every one of them so that
``from app.core.utils import ...`` keeps working unchanged.
"""

from app.core.collection_utils import (
    batch_process,
    chunks,
    deep_get,
    deep_set,
    flatten_dict,
    merge_dicts,
    unflatten_dict,
)
from app.core.datetime_utils import (
    format_datetime,
    parse_datetime,
)
from app.core.decorators import (
    memoize,
    retry,
    timeit,
)
from app.core.file_utils import (
    get_file_extension,
    get_file_size_str,
    get_mime_type,
    is_audio_file,
    is_image_file,
    is_video_file,
)
from app.core.introspection_utils import (
    find_modules,
    get_class_methods,
    get_enum_dict,
    get_enum_names,
    get_enum_values,
    get_function_args,
    get_function_defaults,
    get_subclasses,
    import_string,
)
from app.core.serialization_utils import (
    from_dict,
    from_json,
    is_valid_json,
    safe_json_loads,
    to_dict,
    to_json,
)
from app.core.text_utils import (
    camel_to_snake,
    extract_emails,
    extract_hashtags,
    extract_mentions,
    extract_urls,
    generate_uuid,
    is_email,
    is_phone_number,
    is_url,
    slugify,
    snake_to_camel,
    snake_to_pascal,
    truncate_string,
)
from app.core.url_utils import (
    build_url,
    parse_query_params,
)

__all__ = [
    "batch_process",
    "build_url",
    "camel_to_snake",
    "chunks",
    "deep_get",
    "deep_set",
    "extract_emails",
    "extract_hashtags",
    "extract_mentions",
    "extract_urls",
    "find_modules",
    "flatten_dict",
    "format_datetime",
    "from_dict",
    "from_json",
    "generate_uuid",
    "get_class_methods",
    "get_enum_dict",
    "get_enum_names",
    "get_enum_values",
    "get_file_extension",
    "get_file_size_str",
    "get_function_args",
    "get_function_defaults",
    "get_mime_type",
    "get_subclasses",
    "import_string",
    "is_audio_file",
    "is_email",
    "is_image_file",
    "is_phone_number",
    "is_url",
    "is_valid_json",
    "is_video_file",
    "memoize",
    "merge_dicts",
    "parse_datetime",
    "parse_query_params",
    "retry",
    "safe_json_loads",
    "slugify",
    "snake_to_camel",
    "snake_to_pascal",
    "timeit",
    "to_dict",
    "to_json",
    "truncate_string",
    "unflatten_dict",
]
