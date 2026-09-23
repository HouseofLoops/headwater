"""
String manipulation, identifier generation, and text validation/extraction helpers.

Split out of app.core.utils, which still re-exports every name here.
"""

import re
import uuid
from urllib.parse import urlparse


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


def extract_urls(text: str) -> list[str]:
    """
    Extract URLs from a string.
    
    Args:
        text: The string to extract URLs from
        
    Returns:
        List[str]: List of URLs
    """
    url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+|[^\s<>"]+\.[a-z]{2,}(?:/[^\s<>"]*)?'
    return re.findall(url_pattern, text)


def extract_emails(text: str) -> list[str]:
    """
    Extract email addresses from a string.
    
    Args:
        text: The string to extract email addresses from
        
    Returns:
        List[str]: List of email addresses
    """
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return re.findall(email_pattern, text)


def extract_hashtags(text: str) -> list[str]:
    """
    Extract hashtags from a string.
    
    Args:
        text: The string to extract hashtags from
        
    Returns:
        List[str]: List of hashtags
    """
    hashtag_pattern = r'#[a-zA-Z0-9_]+'
    return re.findall(hashtag_pattern, text)


def extract_mentions(text: str) -> list[str]:
    """
    Extract mentions from a string.
    
    Args:
        text: The string to extract mentions from
        
    Returns:
        List[str]: List of mentions
    """
    mention_pattern = r'@[a-zA-Z0-9_]+'
    return re.findall(mention_pattern, text)
