"""Turn raw dependency-manifest text into (name, current_version) pairs.

Deliberately small and dependency-free. Handles the common cases for a hackathon
demo (npm caret/tilde ranges, pinned PyPI `==`); exotic specifiers degrade
gracefully to current_ver="" rather than raising.
"""

from __future__ import annotations

import json
import re


def parse_package_json(content: str) -> list[tuple[str, str]]:
    """Returns (name, current_ver) for dependencies + devDependencies."""
    data = json.loads(content)
    out: list[tuple[str, str]] = []
    for section in ("dependencies", "devDependencies"):
        for name, spec in (data.get(section) or {}).items():
            out.append((name, _clean_npm_version(spec)))
    return out


def parse_requirements_txt(content: str) -> list[tuple[str, str]]:
    """Returns (name, current_ver) for pinned (`==`) requirements.

    Lines that aren't simple pins (ranges, URLs, comments, blanks, options) are
    skipped — we can't claim a single current version for them.
    """
    out: list[tuple[str, str]] = []
    for raw in content.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)\s*==\s*([A-Za-z0-9._+!-]+)", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _clean_npm_version(spec: str) -> str:
    """Strip range operators (^ ~ >= etc.) to a bare version, best-effort."""
    if not isinstance(spec, str):
        return ""
    # Ignore non-registry specs (git URLs, file:, workspace:, *, latest).
    if any(spec.startswith(p) for p in ("git", "http", "file:", "workspace:")):
        return ""
    if spec in ("*", "latest", ""):
        return ""
    m = re.search(r"(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.]+)?)", spec)
    return m.group(1) if m else ""
