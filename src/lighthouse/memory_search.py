from __future__ import annotations

import re
from typing import Any

from .memory import PostgresMemoryFabric as BaseMemoryFabric


class PostgresMemoryFabric(BaseMemoryFabric):
    """Memory Fabric with filename- and punctuation-aware retrieval."""

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        parts = re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]{2,}", str(query or "").lower())
        terms: list[str] = []
        for part in parts:
            if len(part) >= 2 and part not in terms:
                terms.append(part[:80])
        return terms[:16]

    def search_files(self, *, workspace_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        maximum = max(1, min(int(limit), 100))
        terms = self._query_terms(query)
        clauses = ["workspace_id=%s", "active=TRUE"]
        params: list[Any] = [workspace_id]
        if terms:
            clauses.append("(" + " OR ".join(["name ILIKE %s OR canonical_path ILIKE %s OR search_text ILIKE %s"] * len(terms)) + ")")
            for term in terms:
                pattern = f"%{term}%"
                params.extend([pattern, pattern, pattern])
        params.append(maximum)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT id,canonical_path,relative_path,name,extension,mime_type,size_bytes,
                            modified_at,content_hash,last_opened_at,last_seen_at
                     FROM lh_files WHERE {' AND '.join(clauses)}
                     ORDER BY (last_opened_at IS NOT NULL) DESC,last_opened_at DESC NULLS LAST,
                              last_seen_at DESC LIMIT %s""",
                params,
            ).fetchall()
        return [self._file_dict(row) for row in rows]
