#!/usr/bin/env python3
"""
Version increment utility for Headwater.

Bumps the version according to semantic versioning in every place a release
needs it. release.yml tags whatever app/__version__.py says, so the three must
agree; tests/test_version_consistency.py fails CI if they don't.
"""

import os
import re
import sys

VERSION_FILE = "app/__version__.py"

# (file, pattern with one group for the version, replacement template). Every
# pattern must match exactly once, or the bump aborts before writing anything.
VERSION_LOCATIONS = [
    (VERSION_FILE, r'(__version__\s*=\s*["\'])([^"\']+)(["\'])', r"\g<1>{version}\g<3>"),
    ("Dockerfile", r'(org\.opencontainers\.image\.version=")([^"]+)(")', r"\g<1>{version}\g<3>"),
    ("docs/DOCKERHUB.md", r"(\| `latest`, `)([^`]+)(` \|)", r"\g<1>{version}\g<3>"),
]


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
    """Write the new version to every location, or to none of them."""
    updated = {}
    for path, pattern, template in VERSION_LOCATIONS:
        with open(path) as f:
            content = f.read()
        new_content, count = re.subn(pattern, template.format(version=version), content)
        if count != 1:
            print(f"Error: expected exactly one version string in {path}, found {count}. Nothing was changed.")
            sys.exit(1)
        updated[path] = new_content

    for path, new_content in updated.items():
        with open(path, "w") as f:
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
    print("Version updated in: " + ", ".join(path for path, _, _ in VERSION_LOCATIONS))


if __name__ == "__main__":
    main()
