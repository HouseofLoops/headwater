"""
Enum, function/class introspection, and dynamic import helpers.

Split out of app.core.utils, which still re-exports every name here.
"""

import importlib
import inspect
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, Union


def get_enum_values(enum_class: type) -> list[Any]:
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


def get_enum_names(enum_class: type) -> list[str]:
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


def get_enum_dict(enum_class: type) -> dict[str, Any]:
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


def get_function_args(func: Callable) -> list[str]:
    """
    Get the argument names of a function.
    
    Args:
        func: The function
        
    Returns:
        List[str]: List of argument names
    """
    return list(inspect.signature(func).parameters.keys())


def get_function_defaults(func: Callable) -> dict[str, Any]:
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


def get_class_methods(cls: type) -> list[str]:
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


def get_subclasses(cls: type) -> list[type]:
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


def find_modules(directory: Union[str, Path], recursive: bool = True) -> list[str]:
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
