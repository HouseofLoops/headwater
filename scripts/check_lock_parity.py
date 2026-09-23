#!/usr/bin/env python3
"""Assert requirements.lock actually carries the pins in requirements.txt.

WHY THIS EXISTS
---------------
The Docker image installs from requirements.lock and nothing else:

    COPY requirements.lock .
    RUN pip install --require-hashes -r requirements.lock

Dependabot, however, edits requirements.txt. It has no idea requirements.lock
exists. So a Dependabot bump that is merged without regenerating the lock does
not change the shipped image at all - the old version keeps being installed.

That failure is silent. Every existing gate passes:

  * `pip-audit --requirement requirements.txt` audits the NEW pin, and is happy.
  * `pip-audit --requirement requirements.lock` audits the OLD one, which is
    only red if the bump happened to be security-driven.
  * `assert-lock-parity` in _verify.yml installs FROM the lock and compares the
    result TO the lock, so it agrees with itself and passes.

Nothing compared the two files to each other. This does.

Run it locally the same way CI does:

    python scripts/check_lock_parity.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
LOCK = ROOT / "requirements.lock"

# The command that reproduces requirements.lock. It must compile IN PLACE, over
# the existing file: uv preserves the pins already recorded there and changes
# only what the new constraints force. Compiling to a fresh path instead
# re-resolves the whole graph against today's PyPI and rewrites unrelated
# packages - a one-package bump came out as a 700-line diff when tried that way.
REGEN = "uv pip compile requirements.txt --python-version 3.14 --universal --generate-hashes -o requirements.lock"

PIN = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)\s*==\s*(?P<version>[^\s;#\\]+)")


def normalise(name: str) -> str:
    """PEP 503 normalisation, so Flask-Cors and flask_cors compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = PIN.match(line)
        if match:
            pins[normalise(match.group("name"))] = match.group("version")
    return pins


def main() -> int:
    for path in (REQUIREMENTS, LOCK):
        if not path.exists():
            print(f"::error::{path.name} not found")
            return 1

    declared = read_pins(REQUIREMENTS)
    locked = read_pins(LOCK)

    if not declared:
        print("::error::no pins parsed from requirements.txt - check the format")
        return 1

    missing = sorted(name for name in declared if name not in locked)
    mismatched = sorted(
        (name, want, locked[name]) for name, want in declared.items() if name in locked and locked[name] != want
    )

    if not missing and not mismatched:
        print(f"OK: all {len(declared)} direct pins are present in requirements.lock")
        return 0

    print("::error::requirements.lock is out of date with requirements.txt")
    print()
    print("The image installs from requirements.lock, so until it is regenerated")
    print("these changes do not reach the built image at all.")
    print()

    for name, want, got in mismatched:
        print(f"  {name}: requirements.txt pins {want}, requirements.lock has {got}")
    for name in missing:
        print(f"  {name}: pinned at {declared[name]} but absent from requirements.lock")

    print()
    print("Regenerate it with:")
    print(f"    {REGEN}")
    print()
    print("Commit the result alongside the requirements.txt change.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
