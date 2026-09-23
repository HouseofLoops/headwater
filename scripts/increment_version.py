#!/usr/bin/env python3
"""
Version increment utility for Headwater.

This script increments the version number in app/__version__.py
according to semantic versioning rules.
"""

import os
import re
import sys

VERSION_FILE = "app/__version__.py"


def read_version():
    """Read the current version from the version file."""
    if not os.path.exists(VERSION_FILE):
        print(f"Error: Version file {VERSION_FILE} not found.")
        sys.exit(1)

    with open(VERSION_FILE) as f:
        content = f.read()

    # Extract version using regex
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        print(f"Error: Could not find version string in {VERSION_FILE}.")
        sys.exit(1)

    return match.group(1)


def write_version(version):
    """Write the new version to the version file."""
    with open(VERSION_FILE) as f:
        content = f.read()

    # Replace version using regex
    new_content = re.sub(r'__version__\s*=\s*["\']([^"\']+)["\']', f'__version__ = "{version}"', content)

    with open(VERSION_FILE, "w") as f:
        f.write(new_content)


def increment_version(current_version, increment_type):
    """
    Increment the version according to semantic versioning.

    Args:
        current_version: Current version string (e.g., "1.2.3")
        increment_type: Type of increment ("major", "minor", or "patch")

    Returns:
        New version string
    """
    try:
        # Split version into components
        major, minor, patch = map(int, current_version.split("."))

        # Increment according to type
        if increment_type == "major":
            major += 1
            minor = 0
            patch = 0
        elif increment_type == "minor":
            minor += 1
            patch = 0
        elif increment_type == "patch":
            patch += 1
        else:
            print(f"Error: Unknown increment type '{increment_type}'.")
            print("Valid types are: major, minor, patch")
            sys.exit(1)

        # Construct new version
        new_version = f"{major}.{minor}.{patch}"
        return new_version

    except ValueError:
        print(f"Error: Current version '{current_version}' is not in the format 'X.Y.Z'.")
        sys.exit(1)


def main():
    """Main function."""
    # Check arguments
    if len(sys.argv) != 2 or sys.argv[1] not in ["major", "minor", "patch"]:
        print("Usage: python increment_version.py [major|minor|patch]")
        sys.exit(1)

    increment_type = sys.argv[1]

    # Read current version
    current_version = read_version()
    print(f"Current version: {current_version}")

    # Increment version
    new_version = increment_version(current_version, increment_type)
    print(f"New version: {new_version}")

    # Write new version
    write_version(new_version)
    print(f"Version updated in {VERSION_FILE}")


if __name__ == "__main__":
    main()
