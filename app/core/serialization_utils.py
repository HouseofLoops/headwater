"""
JSON / dict serialization helpers for models and plain values.

Split out of app.core.utils, which still re-exports every name here.
"""

import json
from typing import Any

from fastapi.encoders import jsonable_encoder


def to_json(
    obj: Any,
    exclude_none: bool = True,
    exclude_unset: bool = False,
    exclude_defaults: bool = False,
    by_alias: bool = True,
    **kwargs
) -> str:
    """
    Convert an object to a JSON string.
    
    Args:
        obj: The object to convert
        exclude_none: Whether to exclude None values
        exclude_unset: Whether to exclude unset values
        exclude_defaults: Whether to exclude default values
        by_alias: Whether to use field aliases
        **kwargs: Additional arguments for json.dumps
        
    Returns:
        str: JSON string
    """
    # Convert to a JSON-compatible dict
    json_dict = to_dict(
        obj,
        exclude_none=exclude_none,
        exclude_unset=exclude_unset,
        exclude_defaults=exclude_defaults,
        by_alias=by_alias
    )

    # Set default options for json.dumps
    kwargs.setdefault("ensure_ascii", False)
    kwargs.setdefault("allow_nan", True)
    kwargs.setdefault("indent", None)
    kwargs.setdefault("separators", (",", ":"))

    # Convert to JSON string
    return json.dumps(json_dict, **kwargs)


def to_dict(
    obj: Any,
    exclude_none: bool = True,
    exclude_unset: bool = False,
    exclude_defaults: bool = False,
    by_alias: bool = True
) -> dict[str, Any]:
    """
    Convert an object to a dictionary.
    
    Args:
        obj: The object to convert
        exclude_none: Whether to exclude None values
        exclude_unset: Whether to exclude unset values
        exclude_defaults: Whether to exclude default values
        by_alias: Whether to use field aliases
        
    Returns:
        Dict[str, Any]: Dictionary representation
    """
    return jsonable_encoder(
        obj,
        exclude_none=exclude_none,
        exclude_unset=exclude_unset,
        exclude_defaults=exclude_defaults,
        by_alias=by_alias
    )


def from_dict(data: dict[str, Any], model_class: type) -> Any:
    """
    Convert a dictionary to a model instance.
    
    Args:
        data: The dictionary to convert
        model_class: The model class
        
    Returns:
        Any: Model instance
    """
    return model_class(**data)


def from_json(json_str: str, model_class: type) -> Any:
    """
    Convert a JSON string to a model instance.
    
    Args:
        json_str: The JSON string to convert
        model_class: The model class
        
    Returns:
        Any: Model instance
    """
    data = json.loads(json_str)
    return from_dict(data, model_class)


def is_valid_json(json_str: str) -> bool:
    """
    Check if a string is valid JSON.
    
    Args:
        json_str: The string to check
        
    Returns:
        bool: True if the string is valid JSON
    """
    try:
        json.loads(json_str)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


def safe_json_loads(json_str: str, default: Any = None) -> Any:
    """
    Safely load JSON from a string.
    
    Args:
        json_str: The JSON string to load
        default: The default value if loading fails
        
    Returns:
        Any: The loaded JSON or default
    """
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return default
