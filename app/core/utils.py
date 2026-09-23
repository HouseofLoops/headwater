"""
Utility functions for the Headwater application.

This module provides shared helper functions for common tasks like
datetime formatting, JSON serialization, and other utilities.

The helpers now live in topic modules (datetime_utils, serialization_utils,
text_utils, introspection_utils, collection_utils, decorators, url_utils,
file_utils); this module re-exports every one of them so that
``from app.core.utils import ...`` keeps working unchanged.
"""

from app.core.datetime_utils import (
    format_datetime,
    parse_datetime,
)
from app.core.serialization_utils import (
    to_json,
    to_dict,
    from_dict,
    from_json,
    is_valid_json,
    safe_json_loads,
)
from app.core.text_utils import (
    generate_uuid,
    slugify,
    truncate_string,
    camel_to_snake,
    snake_to_camel,
    snake_to_pascal,
    is_url,
    is_email,
    is_phone_number,
    extract_urls,
    extract_emails,
    extract_hashtags,
    extract_mentions,
)
from app.core.introspection_utils import (
    get_enum_values,
    get_enum_names,
    get_enum_dict,
    get_function_args,
    get_function_defaults,
    get_class_methods,
    get_subclasses,
    import_string,
    find_modules,
)
from app.core.collection_utils import (
    merge_dicts,
    flatten_dict,
    unflatten_dict,
    deep_get,
    deep_set,
    chunks,
    batch_process,
)
from app.core.decorators import (
    retry,
    memoize,
    timeit,
)
from app.core.url_utils import (
    parse_query_params,
    build_url,
)
from app.core.file_utils import (
    get_file_extension,
    is_image_file,
    is_video_file,
    is_audio_file,
    get_file_size_str,
    get_mime_type,
)

__all__ = [
    "format_datetime",
    "parse_datetime",
    "to_json",
    "to_dict",
    "from_dict",
    "from_json",
    "is_valid_json",
    "safe_json_loads",
    "generate_uuid",
    "slugify",
    "truncate_string",
    "camel_to_snake",
    "snake_to_camel",
    "snake_to_pascal",
    "is_url",
    "is_email",
    "is_phone_number",
    "extract_urls",
    "extract_emails",
    "extract_hashtags",
    "extract_mentions",
    "get_enum_values",
    "get_enum_names",
    "get_enum_dict",
    "get_function_args",
    "get_function_defaults",
    "get_class_methods",
    "get_subclasses",
    "import_string",
    "find_modules",
    "merge_dicts",
    "flatten_dict",
    "unflatten_dict",
    "deep_get",
    "deep_set",
    "chunks",
    "batch_process",
    "retry",
    "memoize",
    "timeit",
    "parse_query_params",
    "build_url",
    "get_file_extension",
    "is_image_file",
    "is_video_file",
    "is_audio_file",
    "get_file_size_str",
    "get_mime_type",
]
