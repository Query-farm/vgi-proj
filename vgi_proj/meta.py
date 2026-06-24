"""Shared per-object discovery/description metadata for the proj worker.

The ``vgi-lint`` strict profile (0.26.0) expects these tags on **every**
function (and table, though this worker has none):

- ``vgi.title`` (VGI124) -- a human-friendly display name. It must NOT
  normalize-equal the machine name (lowercase + strip non-alphanumerics), or
  VGI125 fires -- so titles carry an extra descriptive word.
- ``vgi.doc_llm`` (VGI112) -- a Markdown narrative aimed at an LLM/agent
  audience: what it does, when to use it, inputs/outputs, edge cases.
- ``vgi.doc_md`` (VGI113) -- a Markdown narrative for human docs (overview +
  usage + notes). Distinct content from ``doc_llm``.
- ``vgi.keywords`` (VGI126) -- comma-separated search terms/synonyms.
- ``vgi.source_url`` (VGI128) -- link to the implementing source file.

``source_url`` builds the canonical GitHub blob URL so every object points at
exactly where it is implemented.
"""

from __future__ import annotations

REPO_URL = "https://github.com/Query-farm/vgi-proj"

# Base GitHub blob URL for source files in this repo (pinned to ``main``).
_SOURCE_BASE = f"{REPO_URL}/blob/main"


def source_url(relative_path: str) -> str:
    """Build the implementation ``vgi.source_url`` for a repo-relative path.

    Example: ``source_url("vgi_proj/scalars.py")``.
    """
    return f"{_SOURCE_BASE}/{relative_path}"


def object_tags(
    *,
    title: str,
    doc_llm: str,
    doc_md: str,
    keywords: str,
    relative_path: str,
) -> dict[str, str]:
    """Build the five standard per-object discovery/description tags.

    ``relative_path`` is the implementing file relative to the repo root.
    """
    return {
        "vgi.title": title,
        "vgi.doc_llm": doc_llm,
        "vgi.doc_md": doc_md,
        "vgi.keywords": keywords,
        "vgi.source_url": source_url(relative_path),
    }
