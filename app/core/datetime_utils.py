"""
Datetime formatting and parsing helpers.

Split out of app.core.utils, which still re-exports every name here.
"""

from typing import Optional
import datetime



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
