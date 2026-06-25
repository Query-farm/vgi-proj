"""Shared per-object discovery/description metadata for the proj worker.

The ``vgi-lint`` strict profile expects these tags on **every** function (and
table, though this worker has none):

- ``vgi.title`` (VGI124) -- a human-friendly display name. It must NOT
  normalize-equal the machine name (lowercase + strip non-alphanumerics), or
  VGI125 fires -- so titles carry an extra descriptive word.
- ``vgi.doc_llm`` (VGI112) -- a Markdown narrative aimed at an LLM/agent
  audience: what it does, when to use it, inputs/outputs, edge cases.
- ``vgi.doc_md`` (VGI113) -- a Markdown narrative for human docs (overview +
  usage + notes). Distinct content from ``doc_llm``.
- ``vgi.keywords`` (VGI126/VGI138) -- search terms/synonyms, serialized as a
  **JSON array of strings** (not a comma-separated string).

``vgi.source_url`` (VGI139) belongs only on the catalog object, so it is set
once on the catalog and intentionally NOT repeated on every function/schema.
"""

from __future__ import annotations

import json

REPO_URL = "https://github.com/Query-farm/vgi-proj"


def keywords_json(keywords: list[str]) -> str:
    """Serialize keywords as a JSON array of strings for ``vgi.keywords``.

    VGI138 requires ``vgi.keywords`` to be a JSON array (e.g. ``["a","b"]``),
    not a comma-separated string.
    """
    return json.dumps(keywords)


def object_tags(
    *,
    title: str,
    doc_llm: str,
    doc_md: str,
    keywords: list[str],
) -> dict[str, str]:
    """Build the standard per-object discovery/description tags.

    ``keywords`` is a list of search terms/synonyms, serialized to a JSON array
    for ``vgi.keywords`` (VGI138). ``vgi.source_url`` is intentionally omitted
    here -- it belongs only on the catalog (VGI139).
    """
    return {
        "vgi.title": title,
        "vgi.doc_llm": doc_llm,
        "vgi.doc_md": doc_md,
        "vgi.keywords": keywords_json(keywords),
    }
