#!/usr/bin/env python3
"""
Backfill or refresh KB embeddings in SQLite for semantic search (MCP tool ``rag_search_kb``).

Requires an OpenAI-compatible embeddings endpoint (e.g. LlamaStack): set ``ITSM_EMBEDDING_BASE_URL``,
``ITSM_EMBEDDING_MODEL``, and optionally ``ITSM_EMBEDDING_API_KEY``.

Usage:
  export ITSM_DATABASE=/path/to/itsm.db
  export ITSM_EMBEDDING_BASE_URL=https://your-llamastack-host
  export ITSM_EMBEDDING_MODEL=your-embedding-model-id
  export ITSM_EMBEDDING_API_KEY=...   # if required
  python -c \"from app import db; db.init_db()\"
  python scripts/reindex_kb_embeddings.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db
from app.services import kb_embeddings as kb_emb


def main() -> int:
    db.init_db()
    summary = kb_emb.reindex_all_articles()
    print(json.dumps(summary, indent=2))
    return 0 if "error" not in summary else 1


if __name__ == "__main__":
    raise SystemExit(main())
