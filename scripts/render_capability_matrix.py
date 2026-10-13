"""Regenerate the capability table in `docs/CAPABILITIES.md` from the registry.

Why this exists
---------------

The public capability page must never disagree with what the code reports, so its table is
generated from the capability registry. A unit test renders the table on every run and
fails if the committed page differs. Run this script after changing a capability's state,
then commit the page with the change. See ADR-0016.

Usage
-----

    uv run python scripts/render_capability_matrix.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sphereloom.capabilities.matrix import render_section, replace_section
from sphereloom.capabilities.registry import CapabilityRegistry

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PAGE = REPOSITORY_ROOT / "docs" / "CAPABILITIES.md"

#: The backend whose capabilities the public page describes.
PUBLIC_BACKEND = "osc"


def main() -> int:
    """Rewrite the generated section of the capability page in place."""
    document = PAGE.read_text(encoding="utf-8")
    updated = replace_section(document, render_section(CapabilityRegistry(PUBLIC_BACKEND)))
    if updated == document:
        print("docs/CAPABILITIES.md is up to date")
        return 0
    PAGE.write_text(updated, encoding="utf-8", newline="\n")
    print("updated docs/CAPABILITIES.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
