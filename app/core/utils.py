"""
Utility functions for the Headwater application.

This module provides shared helper functions for common tasks like
datetime formatting, JSON serialization, and other utilities.
"""
from typing import Any, Dict, List, Optional, Union, Set, TypeVar, Generic, Callable, Tuple
import json
import datetime
import re
import uuid
import logging
import inspect
from enum import Enum
from pathlib import Path
import os
import importlib
import sys
from urllib.parse import urlparse, parse_qs, urlencode

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

# Configure logger
logger = logging.getLogger(__name__)

# Type variable for generic functions
T = TypeVar('T')


def format_datetime(
    dt: Optional[datetime.datetime] = None,
    format_str: str = "%Y-%m-%d %H:%M:%S"
) -> str:
    """
    Format a datetime object as a string.
    
    Args:
        dt: The datetime object to format (default: now)
        format_str: The format string
        
    Returns:
        str: Formatted datetime string
    """
    if dt is None:
        dt = datetime.datetime.now()
    
    return dt.strftime(format_str)


def parse_datetime(
    dt_str: str,
    format_str: str = "%Y-%m-%d %H:%M:%S"
) -> datetime.datetime:
    """
    Parse a string into a datetime object.
    
    Args:
        dt_str: The datetime string to parse
        format_str: The format string
        
    Returns:
        datetime.datetime: Parsed datetime object
        
    Raises:
        ValueError: If the string cannot be parsed
    """
    return datetime.datetime.strptime(dt_str, format_str)


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
) -> Dict[str, Any]:
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


def from_dict(data: Dict[str, Any], model_class: type) -> Any:
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


def generate_uuid() -> str:
    """
    Generate a UUID string.
    
    Returns:
        str: UUID string
    """
    return str(uuid.uuid4())


def slugify(text: str) -> str:
    """
    Convert a string to a slug.
    
    Args:
        text: The string to convert
        
    Returns:
        str: Slug
    """
    # Convert to lowercase
    text = text.lower()
    
    # Remove non-alphanumeric characters
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    
    # Replace spaces with hyphens
    text = re.sub(r'\s+', '-', text)
    
    # Remove consecutive hyphens
    text = re.sub(r'-+', '-', text)
    
    # Remove leading and trailing hyphens
    text = text.strip('-')
    
    return text


def truncate_string(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate a string to a maximum length.
    
    Args:
        text: The string to truncate
        max_length: The maximum length
        suffix: The suffix to add if truncated
        
    Returns:
        str: Truncated string
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix


def camel_to_snake(name: str) -> str:
    """
    Convert a camelCase string to snake_case.
    
    Args:
        name: The string to convert
        
    Returns:
        str: snake_case string
    """
    # Insert underscore before uppercase letters and convert to lowercase
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def snake_to_camel(name: str) -> str:
    """
    Convert a snake_case string to camelCase.
    
    Args:
        name: The string to convert
        
    Returns:
        str: camelCase string
    """
    # Split by underscore and join with first part lowercase, rest capitalized
    components = name.split('_')
    return components[0] + ''.join(x.title() for x in components[1:])


def snake_to_pascal(name: str) -> str:
    """
    Convert a snake_case string to PascalCase.
    
    Args:
        name: The string to convert
        
    Returns:
        str: PascalCase string
    """
    # Split by underscore and join with all parts capitalized
    return ''.join(x.title() for x in name.split('_'))


def get_enum_values(enum_class: type) -> List[Any]:
    """
    Get the values of an Enum class.
    
    Args:
        enum_class: The Enum class
        
    Returns:
        List[Any]: List of enum values
    """
    if not issubclass(enum_class, Enum):
        raise TypeError(f"{enum_class.__name__} is not an Enum class")
    
    return [item.value for item in enum_class]


def get_enum_names(enum_class: type) -> List[str]:
    """
    Get the names of an Enum class.
    
    Args:
        enum_class: The Enum class
        
    Returns:
        List[str]: List of enum names
    """
    if not issubclass(enum_class, Enum):
        raise TypeError(f"{enum_class.__name__} is not an Enum class")
    
    return [item.name for item in enum_class]


def get_enum_dict(enum_class: type) -> Dict[str, Any]:
    """
    Get a dictionary of an Enum class.
    
    Args:
        enum_class: The Enum class
        
    Returns:
        Dict[str, Any]: Dictionary of enum names and values
    """
    if not issubclass(enum_class, Enum):
        raise TypeError(f"{enum_class.__name__} is not an Enum class")
    
    return {item.name: item.value for item in enum_class}


def get_function_args(func: Callable) -> List[str]:
    """
    Get the argument names of a function.
    
    Args:
        func: The function
        
    Returns:
        List[str]: List of argument names
    """
    return list(inspect.signature(func).parameters.keys())


def get_function_defaults(func: Callable) -> Dict[str, Any]:
    """
    Get the default values of a function's arguments.
    
    Args:
        func: The function
        
    Returns:
        Dict[str, Any]: Dictionary of argument names and default values
    """
    signature = inspect.signature(func)
    return {
        k: v.default
        for k, v in signature.parameters.items()
        if v.default is not inspect.Parameter.empty
    }


def get_class_methods(cls: type) -> List[str]:
    """
    Get the method names of a class.
    
    Args:
        cls: The class
        
    Returns:
        List[str]: List of method names
    """
    return [
        name for name, value in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith('_')
    ]


def get_subclasses(cls: type) -> List[type]:
    """
    Get all subclasses of a class.
    
    Args:
        cls: The class
        
    Returns:
        List[type]: List of subclasses
    """
    subclasses = []
    
    for subclass in cls.__subclasses__():
        subclasses.append(subclass)
        subclasses.extend(get_subclasses(subclass))
    
    return subclasses


def import_string(dotted_path: str) -> Any:
    """
    Import a dotted module path and return the attribute/class designated by the
    last name in the path.
    
    Args:
        dotted_path: The dotted path to import
        
    Returns:
        Any: The imported attribute/class
        
    Raises:
        ImportError: If the import failed
    """
    try:
        module_path, class_name = dotted_path.rsplit('.', 1)
    except ValueError as e:
        raise ImportError(f"{dotted_path} doesn't look like a module path") from e
    
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(f"Could not import {module_path}") from e
    
    try:
        return getattr(module, class_name)
    except AttributeError as e:
        raise ImportError(f"Module {module_path} does not define a {class_name} attribute/class") from e


def find_modules(directory: Union[str, Path], recursive: bool = True) -> List[str]:
    """
    Find all Python modules in a directory.
    
    Args:
        directory: The directory to search
        recursive: Whether to search recursively
        
    Returns:
        List[str]: List of module names
    """
    directory = Path(directory)
    modules = []
    
    for item in directory.iterdir():
        if item.is_file() and item.suffix == '.py' and item.name != '__init__.py':
            modules.append(item.stem)
        elif recursive and item.is_dir() and (item / '__init__.py').exists():
            # It's a package
            sub_modules = find_modules(item, recursive)
            modules.extend(f"{item.name}.{sub_module}" for sub_module in sub_modules)
    
    return modules


def merge_dicts(dict1: Dict[str, Any], dict2: Dict[str, Any]) -> Dict[str, Any]:
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


def flatten_dict(
    d: Dict[str, Any],
    parent_key: str = '',
    separator: str = '.'
) -> Dict[str, Any]:
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


def unflatten_dict(
    d: Dict[str, Any],
    separator: str = '.'
) -> Dict[str, Any]:
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


def deep_get(
    d: Dict[str, Any],
    keys: Union[str, List[str]],
    default: Any = None,
    separator: str = '.'
) -> Any:
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


def deep_set(
    d: Dict[str, Any],
    keys: Union[str, List[str]],
    value: Any,
    separator: str = '.'
) -> Dict[str, Any]:
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


def chunks(lst: List[T], n: int) -> List[List[T]]:
    """
    Split a list into chunks of size n.
    
    Args:
        lst: The list to split
        n: The chunk size
        
    Returns:
        List[List[T]]: List of chunks
    """
    return [lst[i:i + n] for i in range(0, len(lst), n)]


def batch_process(
    items: List[T],
    process_func: Callable[[List[T]], List[Any]],
    batch_size: int = 100
) -> List[Any]:
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


def retry(
    func: Callable,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    exceptions: Union[type, Tuple[type, ...]] = Exception,
    logger: Optional[logging.Logger] = None
):
    """
    Retry a function on failure.
    
    Args:
        func: The function to retry
        max_retries: The maximum number of retries
        retry_delay: The delay between retries in seconds
        exceptions: The exceptions to catch
        logger: Optional logger
        
    Returns:
        Callable: Decorated function
    """
    def decorator(*args, **kwargs):
        last_exception = None
        
        for attempt in range(max_retries + 1):
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                last_exception = e
                
                if attempt < max_retries:
                    if logger:
                        logger.warning(
                            f"Retry {attempt + 1}/{max_retries} for {func.__name__} "
                            f"after error: {str(e)}"
                        )
                    
                    # Wait before retrying
                    import time
                    time.sleep(retry_delay)
                else:
                    if logger:
                        logger.error(
                            f"Failed all {max_retries} retries for {func.__name__}: {str(e)}"
                        )
        
        # If we get here, all retries failed
        raise last_exception
    
    return decorator


def memoize(func: Callable):
    """
    Memoize a function's results.
    
    Args:
        func: The function to memoize
        
    Returns:
        Callable: Decorated function
    """
    cache = {}
    
    def wrapper(*args, **kwargs):
        # Create a key from the arguments
        key = str(args) + str(sorted(kwargs.items()))
        
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        
        return cache[key]
    
    return wrapper


def timeit(func: Callable):
    """
    Time a function's execution.
    
    Args:
        func: The function to time
        
    Returns:
        Callable: Decorated function
    """
    def wrapper(*args, **kwargs):
        import time
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        
        logger.debug(f"{func.__name__} took {end_time - start_time:.6f} seconds")
        
        return result
    
    return wrapper


def parse_query_params(url: str) -> Dict[str, List[str]]:
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
    path: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None
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


def get_file_extension(filename: str) -> str:
    """
    Get the extension of a file.
    
    Args:
        filename: The filename
        
    Returns:
        str: The file extension
    """
    return os.path.splitext(filename)[1].lower()


def is_image_file(filename: str) -> bool:
    """
    Check if a file is an image.
    
    Args:
        filename: The filename
        
    Returns:
        bool: True if the file is an image
    """
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'}
    return get_file_extension(filename) in image_extensions


def is_video_file(filename: str) -> bool:
    """
    Check if a file is a video.
    
    Args:
        filename: The filename
        
    Returns:
        bool: True if the file is a video
    """
    video_extensions = {'.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm'}
    return get_file_extension(filename) in video_extensions


def is_audio_file(filename: str) -> bool:
    """
    Check if a file is an audio file.
    
    Args:
        filename: The filename
        
    Returns:
        bool: True if the file is an audio file
    """
    audio_extensions = {'.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a'}
    return get_file_extension(filename) in audio_extensions


def get_file_size_str(size_bytes: int) -> str:
    """
    Get a human-readable file size string.
    
    Args:
        size_bytes: The file size in bytes
        
    Returns:
        str: Human-readable file size
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def get_mime_type(filename: str) -> str:
    """
    Get the MIME type of a file.
    
    Args:
        filename: The filename
        
    Returns:
        str: The MIME type
    """
    import mimetypes
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or 'application/octet-stream'


def is_url(text: str) -> bool:
    """
    Check if a string is a URL.
    
    Args:
        text: The string to check
        
    Returns:
        bool: True if the string is a URL
    """
    try:
        result = urlparse(text)
        return all([result.scheme, result.netloc])
    except:
        return False


def is_email(text: str) -> bool:
    """
    Check if a string is an email address.
    
    Args:
        text: The string to check
        
    Returns:
        bool: True if the string is an email address
    """
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, text))


def is_phone_number(text: str) -> bool:
    """
    Check if a string is a phone number.
    
    Args:
        text: The string to check
        
    Returns:
        bool: True if the string is a phone number
    """
    # Remove non-digit characters
    digits = re.sub(r'\D', '', text)
    
    # Check if the result has a valid length for a phone number
    return 7 <= len(digits) <= 15


def extract_urls(text: str) -> List[str]:
    """
    Extract URLs from a string.
    
    Args:
        text: The string to extract URLs from
        
    Returns:
        List[str]: List of URLs
    """
    url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+|[^\s<>"]+\.[a-z]{2,}(?:/[^\s<>"]*)?'
    return re.findall(url_pattern, text)


def extract_emails(text: str) -> List[str]:
    """
    Extract email addresses from a string.
    
    Args:
        text: The string to extract email addresses from
        
    Returns:
        List[str]: List of email addresses
    """
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return re.findall(email_pattern, text)


def extract_hashtags(text: str) -> List[str]:
    """
    Extract hashtags from a string.
    
    Args:
        text: The string to extract hashtags from
        
    Returns:
        List[str]: List of hashtags
    """
    hashtag_pattern = r'#[a-zA-Z0-9_]+'
    return re.findall(hashtag_pattern, text)


def extract_mentions(text: str) -> List[str]:
    """
    Extract mentions from a string.
    
    Args:
        text: The string to extract mentions from
        
    Returns:
        List[str]: List of mentions
    """
    mention_pattern = r'@[a-zA-Z0-9_]+'
    return re.findall(mention_pattern, text)
