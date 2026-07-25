from __future__ import annotations

import json
import re
from typing import Any, Iterable
from uuid import uuid4


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _terms(value: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", str(value or "").lower())
    words = {part for part in normalized.split() if part}
    expanded = set(words)
    for word in words:
        if len(word) > 4 and word.endswith("s"):
            expanded.add(word[:-1])
        if len(word) > 5 and word.endswith("ing"):
            expanded.add(word[:-3])
    for run in re.findall(r"[\u3400-\u9fff]+", normalized):
        expanded.add(run)
        expanded.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    return expanded


def _category(tool_name: str) -> str:
    parts = str(tool_name or "").split(".")
    if tool_name.startswith("agent.bus."):
        return "agent-collaboration"
    if tool_name.startswith("project."):
        return "mega-project"
    if tool_name.startswith("tools."):
        return "tool-discovery"
    if tool_name.startswith("data."):
        return "data"
    if tool_name.startswith("desktop."):
        return "desktop"
    if tool_name.startswith("system.git."):
        return "repository"
    if tool_name.startswith("system.test."):
        return "testing"
    if tool_name.startswith("system.file.") or tool_name.startswith("system.project."):
        return "repository"
    return parts[0] if parts else "general"


class PostgresToolRegistry:
    """Durable, searchable knowledge about tools available to the main AI and Agents."""

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Tool Registry requires psycopg") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    def sync_capabilities(self, capabilities: Iterable[Any]) -> int:
        count = 0
        with self._connect() as connection:
            for capability in capabilities:
                public = capability.public_dict()
                tool_name = str(public["tool_name"])
                row = connection.execute(
                    """INSERT INTO lh_tools(
                           id,tool_name,version,title,description,category,
                           execution_type,kernel,risk,confirmation_mode,writes,
                           arguments,capabilities,requirements,examples,metadata,active
                       ) VALUES (%s,%s,'v1',%s,%s,%s,'capability',%s,%s,%s,%s,
                                 %s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,TRUE)
                       ON CONFLICT (tool_name,version) DO UPDATE SET
                         title=EXCLUDED.title,
                         description=EXCLUDED.description,
                         category=EXCLUDED.category,
                         execution_type=EXCLUDED.execution_type,
                         kernel=EXCLUDED.kernel,
                         risk=EXCLUDED.risk,
                         confirmation_mode=EXCLUDED.confirmation_mode,
                         writes=EXCLUDED.writes,
                         arguments=EXCLUDED.arguments,
                         capabilities=EXCLUDED.capabilities,
                         requirements=EXCLUDED.requirements,
                         examples=EXCLUDED.examples,
                         metadata=lh_tools.metadata || EXCLUDED.metadata,
                         active=TRUE,
                         updated_at=now()
                       RETURNING id""",
                    (
                        str(uuid4()),
                        tool_name,
                        str(public.get("command") or tool_name),
                        str(public.get("description") or ""),
                        _category(tool_name),
                        str(public.get("kernel") or ""),
                        str(public.get("risk") or ""),
                        str(public.get("confirmation") or ""),
                        bool(public.get("writes")),
                        _json(public.get("arguments") or {}),
                        _json([tool_name, *(public.get("aliases") or [])]),
                        _json({"kernel": public.get("kernel")}),
                        _json([]),
                        _json({"aliases": public.get("aliases") or []}),
                    ),
                ).fetchone()
                if row:
                    count += 1
        return count

    def search(
        self,
        query: str,
        *,
        categories: list[str] | tuple[str, ...] = (),
        limit: int = 20,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        limit = max(1, min(int(limit), 100))
        clauses = [] if include_inactive else ["active=TRUE"]
        params: list[Any] = []
        if categories:
            clauses.append("category = ANY(%s)")
            params.append(list(categories))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM lh_tools{where}
                    ORDER BY updated_at DESC,tool_name LIMIT 500""",
                params,
            ).fetchall()

        query_lower = query.lower()
        query_terms = _terms(query)
        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for row in rows:
            value = self._tool_dict(row)
            aliases = value.get("capabilities") if isinstance(value.get("capabilities"), list) else []
            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            metadata_aliases = metadata.get("aliases") if isinstance(metadata.get("aliases"), list) else []
            haystack = " ".join(
                [
                    str(value.get("tool_name") or ""),
                    str(value.get("title") or ""),
                    str(value.get("description") or ""),
                    str(value.get("category") or ""),
                    *(str(item) for item in aliases),
                    *(str(item) for item in metadata_aliases),
                ]
            ).lower()
            if not query:
                score = 1.0
            else:
                haystack_terms = _terms(haystack)
                matched = query_terms & haystack_terms
                score = float(len(matched))
                if query_lower in haystack:
                    score += 8.0
                tool_name = str(value.get("tool_name") or "").lower()
                title = str(value.get("title") or "").lower()
                if query_lower == tool_name or query_lower == title:
                    score += 20.0
                if not score:
                    continue
            value["relevance"] = score / max(1.0, float(len(query_terms) or 1))
            ranked.append((-score, str(value.get("tool_name") or ""), value))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [value for _score, _name, value in ranked[:limit]]

    def inspect(self, tool_name: str, *, version: str = "v1") -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM lh_tools
                   WHERE tool_name=%s AND version=%s ORDER BY updated_at DESC LIMIT 1""",
                (str(tool_name), str(version)),
            ).fetchone()
            if not row:
                raise KeyError("tool not found")
            relations = connection.execute(
                """SELECT relation,confidence,metadata,t.tool_name AS related_tool
                   FROM lh_tool_relations r
                   JOIN lh_tools t ON t.id=r.to_tool_id
                   WHERE r.from_tool_id=%s ORDER BY relation,t.tool_name""",
                (row["id"],),
            ).fetchall()
        value = self._tool_dict(row)
        value["relations"] = [dict(item) for item in relations]
        return value

    def recommend(
        self,
        query: str,
        *,
        workspace_id: str | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        tools = self.search(query, limit=limit)
        recommendations = []
        for item in tools:
            recommendations.append(
                {
                    "tool_name": item["tool_name"],
                    "category": item["category"],
                    "why_relevant": item["description"],
                    "confidence": min(0.95, 0.55 + 0.08 * float(item.get("relevance") or 0.0)),
                    "estimated_cost": {
                        "latency": "background" if item["category"] in {"agent-collaboration", "mega-project"} else "direct",
                        "model_calls": "variable" if item["tool_name"].startswith("agent.bus.") else "none_or_tool_defined",
                    },
                    "risk": item.get("risk"),
                    "advisory_only": True,
                }
            )
        project_tools = [
            item for item in recommendations
            if item["category"] in {"mega-project", "agent-collaboration", "repository", "testing"}
        ]
        return {
            "query": str(query or ""),
            "workspace_id": workspace_id,
            "run_id": run_id,
            "project_id": project_id,
            "recommendations": recommendations,
            "scale_advice": {
                "recommendation": "consider_mega_project_mode" if len(project_tools) >= 3 else "no_scale_preference",
                "signals": [item["tool_name"] for item in project_tools[:6]],
                "advisory_only": True,
                "main_ai_decides": True,
            },
            "tool_search_available": True,
        }

    def categories(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT category,count(*) AS tool_count
                   FROM lh_tools WHERE active=TRUE GROUP BY category ORDER BY category"""
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _tool_dict(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["id"] = str(value["id"])
        value.pop("search_vector", None)
        for key in ("created_at", "updated_at"):
            if value.get(key):
                value[key] = value[key].isoformat()
        if "relevance" in value:
            value["relevance"] = float(value["relevance"] or 0.0)
        return value
