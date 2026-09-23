"""
Dict and list manipulation helpers (merge, flatten, deep access, chunking).

Split out of app.core.utils, which still re-exports every name here.
"""

from collections.abc import Callable
from typing import Any


def merge_dicts(dict1: dict[str, Any], dict2: dict[str, Any]) -> dict[str, Any]:
    """
    Merge two dictionaries recursively.

    Args:
        dict1: The first dictionary
        dict2: The second dictionary

    Returns:
        Dict[str, Any]: Merged dictionary
    """
    result = dict1.copy()

    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value

    return result


def flatten_dict(d: dict[str, Any], parent_key: str = "", separator: str = ".") -> dict[str, Any]:
    """
    Flatten a nested dictionary.

    Args:
        d: The dictionary to flatten
        parent_key: The parent key
        separator: The separator for nested keys

    Returns:
        Dict[str, Any]: Flattened dictionary
    """
    items = []

    for k, v in d.items():
        new_key = f"{parent_key}{separator}{k}" if parent_key else k

        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, separator).items())
        else:
            items.append((new_key, v))

    return dict(items)


def unflatten_dict(d: dict[str, Any], separator: str = ".") -> dict[str, Any]:
    """
    Unflatten a flattened dictionary.

    Args:
        d: The dictionary to unflatten
        separator: The separator for nested keys

    Returns:
        Dict[str, Any]: Unflattened dictionary
    """
    result = {}

    for key, value in d.items():
        parts = key.split(separator)

        # Start with the result dictionary
        current = result

        # Navigate through the parts
        for part in parts[:-1]:
            # Create nested dictionaries as needed
            if part not in current:
                current[part] = {}
            current = current[part]

        # Set the value at the final part
        current[parts[-1]] = value

    return result


def deep_get(d: dict[str, Any], keys: str | list[str], default: Any = None, separator: str = ".") -> Any:
    """
    Get a value from a nested dictionary using a dotted path.

    Args:
        d: The dictionary
        keys: The dotted path or list of keys
        default: The default value if the path doesn't exist
        separator: The separator for the dotted path

    Returns:
        Any: The value at the path or the default
    """
    if isinstance(keys, str):
        keys = keys.split(separator)

    current = d

    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]

    return current


def deep_set(d: dict[str, Any], keys: str | list[str], value: Any, separator: str = ".") -> dict[str, Any]:
    """
    Set a value in a nested dictionary using a dotted path.

    Args:
        d: The dictionary
        keys: The dotted path or list of keys
        value: The value to set
        separator: The separator for the dotted path

    Returns:
        Dict[str, Any]: The modified dictionary
    """
    if isinstance(keys, str):
        keys = keys.split(separator)

    current = d

    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]

    current[keys[-1]] = value

    return d


def chunks[T](lst: list[T], n: int) -> list[list[T]]:
    """
    Split a list into chunks of size n.

    Args:
        lst: The list to split
        n: The chunk size

    Returns:
        List[List[T]]: List of chunks
    """
    return [lst[i : i + n] for i in range(0, len(lst), n)]


def batch_process[T](items: list[T], process_func: Callable[[list[T]], list[Any]], batch_size: int = 100) -> list[Any]:
    """
    Process a list of items in batches.

    Args:
        items: The items to process
        process_func: The function to process each batch
        batch_size: The batch size

    Returns:
        List[Any]: List of processed results
    """
    results = []

    for batch in chunks(items, batch_size):
        batch_results = process_func(batch)
        results.extend(batch_results)

    return results
