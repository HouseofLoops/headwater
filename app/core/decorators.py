"""
Function decorators: retry, memoize, timeit.

Split out of app.core.utils, which still re-exports every name here.
"""

import logging
from collections.abc import Callable

# Configure logger
logger = logging.getLogger(__name__)


def retry(
    func: Callable,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    exceptions: type | tuple[type, ...] = Exception,
    logger: logging.Logger | None = None,
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
                        logger.warning(f"Retry {attempt + 1}/{max_retries} for {func.__name__} after error: {e!s}")

                    # Wait before retrying
                    import time

                    time.sleep(retry_delay)
                else:
                    if logger:
                        logger.error(f"Failed all {max_retries} retries for {func.__name__}: {e!s}")

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
