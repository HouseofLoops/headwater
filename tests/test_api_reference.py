"""docs/API_REFERENCE.md must match what the generator renders from the live OpenAPI schema.

The reference used to be hand-written and drifted (26 of 54 /api/v1 paths
documented). It is now generated; this test fails whenever an endpoint changes
without the file being regenerated.
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_api_reference.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_api_reference", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_reference_matches_openapi_schema():
    generator = _load_generator()
    expected = generator.render(generator.load_openapi())
    committed = generator.OUTPUT_PATH.read_text(encoding="utf-8")

    assert committed == expected, (
        "docs/API_REFERENCE.md is out of date with the OpenAPI schema. "
        "Regenerate it with `python scripts/generate_api_reference.py` and commit the result."
    )


def test_render_ignores_environment_specific_info():
    """info (version, title, description) comes from settings and must not leak into the doc."""
    generator = _load_generator()
    schema = generator.load_openapi()
    baseline = generator.render(schema)

    varied = dict(schema)
    varied["info"] = {"title": "Other", "version": "0.0.0-test", "description": "different"}
    varied["servers"] = [{"url": "https://example.invalid"}]

    assert generator.render(varied) == baseline
