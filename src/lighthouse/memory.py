from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


_TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".html", ".htm", ".css", ".js", ".jsx", ".ts", ".tsx",
    ".py", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sql", ".sh",
    ".xml", ".csv", ".env", ".vue", ".svelte", ".java", ".go", ".rs", ".c",
    ".h", ".cpp", ".hpp",
}
_SKIP_DIRECTORIES = {
    ".git", ".svn", ".hg", "node_modules", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".next",
    ".cache", "Library", ".Trash",
}
_REFERENTIAL_MARKERS = (
    "繼續", "继续", "再", "剛才", "刚才", "之前", "這個", "这个", "那個", "那个",
    "它", "豐富", "丰富", "優化", "优化", "continue", "again", "more", "richer",
    "this", "that", "previous", "before",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _tokens(value: str) -> list[str]:
    words: list[str] = []
    for raw in str(value or "").lower().replace("/", " ").replace("_", " ").split():
        token = "".join(char for char in raw if char.isalnum() or "\u3400" <= char <= "\u9fff")
        if len(token) >= 2:
            words.append(token[:80])
    return words[:12]


def _looks_referential(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return any(marker in normalized for marker in _REFERENTIAL_MARKERS)


class PostgresMemoryFabric:
    """Durable conversation, task, locator and file memory for LightHouse.

    Files remain the source of truth. PostgreSQL stores bounded metadata,
    searchable text, relationships and references to successful Receipts.
    """

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Memory Fabric requires psycopg") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    def ensure_conversation(
        self,
        *,
        workspace_id: str,
        actor: str,
        conversation_id: str | None = None,
        new: bool = False,
        title: str | None = None,
    ) -> dict[str, Any]:
        actor = str(actor or "").strip()
        if not actor:
            raise ValueError("actor is required")
        with self._connect() as connection:
            row = None
            if conversation_id and not new:
                row = connection.execute(
                    """SELECT * FROM lh_conversations
                       WHERE id=%s AND workspace_id=%s AND actor=%s""",
                    (conversation_id, workspace_id, actor),
                ).fetchone()
            if row is None and not new and not conversation_id:
                row = connection.execute(
                    """SELECT * FROM lh_conversations
                       WHERE workspace_id=%s AND actor=%s AND status='active'
                       ORDER BY last_message_at DESC NULLS LAST,updated_at DESC LIMIT 1""",
                    (workspace_id, actor),
                ).fetchone()
            if row is None:
                row = connection.execute(
                    """INSERT INTO lh_conversations(id,workspace_id,actor,title,status)
                       VALUES (%s,%s,%s,%s,'active') RETURNING *""",
                    (str(uuid4()), workspace_id, actor, title),
                ).fetchone()
        return self._conversation_dict(row)

    def link_run(self, run_id: str, conversation_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO lh_run_conversations(run_id,conversation_id)
                   VALUES (%s,%s) ON CONFLICT (run_id) DO UPDATE
                   SET conversation_id=EXCLUDED.conversation_id""",
                (run_id, conversation_id),
            )

    def conversation_for_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT c.* FROM lh_run_conversations rc
                   JOIN lh_conversations c ON c.id=rc.conversation_id
                   WHERE rc.run_id=%s""",
                (run_id,),
            ).fetchone()
        return self._conversation_dict(row) if row else None

    def record_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        role = str(role or "").strip().lower()
        if role not in {"user", "assistant", "system"}:
            raise ValueError("message role is invalid")
        content = str(content or "").strip()
        if not content:
            raise ValueError("message content is required")
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_messages(conversation_id,run_id,role,content,metadata)
                   VALUES (%s,%s,%s,%s,%s::jsonb) RETURNING *""",
                (conversation_id, run_id, role, content, _json(metadata or {})),
            ).fetchone()
            connection.execute(
                """UPDATE lh_conversations SET updated_at=now(),last_message_at=now(),
                   title=COALESCE(title,%s) WHERE id=%s""",
                (content[:120] if role == "user" else None, conversation_id),
            )
        return dict(row)

    def start_task(self, *, run_id: str, conversation_id: str, goal: str) -> dict[str, Any]:
        with self._connect() as connection:
            conversation = connection.execute(
                "SELECT * FROM lh_conversations WHERE id=%s",
                (conversation_id,),
            ).fetchone()
            if not conversation:
                raise KeyError("conversation not found")
            recent_subject = None
            if _looks_referential(goal):
                recent_subject = connection.execute(
                    """SELECT subject_locator_id FROM lh_memory_tasks
                       WHERE workspace_id=%s AND actor=%s AND subject_locator_id IS NOT NULL
                       ORDER BY updated_at DESC LIMIT 1""",
                    (conversation["workspace_id"], conversation["actor"]),
                ).fetchone()
            task_id = str(uuid4())
            row = connection.execute(
                """INSERT INTO lh_memory_tasks(
                       id,workspace_id,conversation_id,actor,goal,status,
                       subject_locator_id,last_run_id
                   ) VALUES (%s,%s,%s,%s,%s,'active',%s,%s) RETURNING *""",
                (
                    task_id,
                    conversation["workspace_id"],
                    conversation_id,
                    conversation["actor"],
                    goal,
                    recent_subject["subject_locator_id"] if recent_subject else None,
                    run_id,
                ),
            ).fetchone()
            connection.execute(
                "UPDATE lh_conversations SET active_task_id=%s,updated_at=now() WHERE id=%s",
                (task_id, conversation_id),
            )
        return dict(row)

    def update_task_input(self, run_id: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE lh_memory_tasks SET updated_at=now(),last_run_id=%s,
                   summary=CASE WHEN summary IS NULL OR summary='' THEN %s
                                ELSE summary || E'\n' || %s END
                   WHERE id=(SELECT c.active_task_id FROM lh_run_conversations rc
                             JOIN lh_conversations c ON c.id=rc.conversation_id
                             WHERE rc.run_id=%s)""",
                (run_id, message[:1000], message[:1000], run_id),
            )

    def complete_task(self, run_id: str, *, status: str, summary: str | None) -> None:
        normalized = "completed" if status == "succeeded" else "failed" if status == "failed" else status
        with self._connect() as connection:
            connection.execute(
                """UPDATE lh_memory_tasks SET status=%s,summary=COALESCE(%s,summary),
                   last_run_id=%s,updated_at=now()
                   WHERE id=(SELECT c.active_task_id FROM lh_run_conversations rc
                             JOIN lh_conversations c ON c.id=rc.conversation_id
                             WHERE rc.run_id=%s)""",
                (normalized, summary, run_id, run_id),
            )

    def project_operation(self, run_id: str, snapshot: dict[str, Any]) -> None:
        operation = snapshot.get("operation") if isinstance(snapshot.get("operation"), dict) else {}
        receipt = snapshot.get("receipt") if isinstance(snapshot.get("receipt"), dict) else {}
        if receipt.get("ok") is not True:
            return
        result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
        capability = str(operation.get("capability") or "")
        envelope = operation.get("envelope") if isinstance(operation.get("envelope"), dict) else {}
        arguments = envelope.get("arguments") if isinstance(envelope.get("arguments"), dict) else {}
        with self._connect() as connection:
            context = connection.execute(
                """SELECT c.workspace_id,c.actor,c.id AS conversation_id,c.active_task_id
                   FROM lh_run_conversations rc
                   JOIN lh_conversations c ON c.id=rc.conversation_id
                   WHERE rc.run_id=%s""",
                (run_id,),
            ).fetchone()
        if not context:
            return

        locator_kind: str | None = None
        value: str | None = None
        label: str | None = None
        if capability in {"system.file.write.v1", "desktop.file.open.v1"}:
            locator_kind = "file"
            value = str(result.get("path") or arguments.get("path") or "").strip()
            label = Path(value).name if value else None
        elif capability == "system.file.read.v1":
            path = str(result.get("path") or arguments.get("path") or "").strip()
            cwd = str(result.get("cwd") or arguments.get("cwd") or "").strip()
            value = str(Path(cwd, path).resolve()) if cwd and path and not Path(path).is_absolute() else path
            locator_kind = "file" if value else None
            label = Path(value).name if value else None
        elif capability == "desktop.browser.open_url.v1":
            locator_kind = "url"
            value = str(result.get("url") or arguments.get("url") or "").strip()
            label = value
        elif capability == "system.directory.create.v1":
            locator_kind = "directory"
            value = str(result.get("path") or arguments.get("path") or "").strip()
            label = Path(value).name if value else None

        if not locator_kind or not value:
            return
        canonical = self._canonical_locator(locator_kind, value)
        locator = self.upsert_locator(
            workspace_id=str(context["workspace_id"]),
            kind=locator_kind,
            canonical_value=canonical,
            display_value=value,
            label=label,
            metadata={"capability": capability, "operation_id": operation.get("id")},
        )
        with self._connect() as connection:
            connection.execute(
                """UPDATE lh_conversations SET active_subject_kind=%s,
                   active_subject_value=%s,updated_at=now() WHERE id=%s""",
                (locator_kind, canonical, context["conversation_id"]),
            )
            if context.get("active_task_id"):
                connection.execute(
                    """UPDATE lh_memory_tasks SET subject_locator_id=%s,last_run_id=%s,
                       updated_at=now() WHERE id=%s""",
                    (locator["id"], run_id, context["active_task_id"]),
                )
        if locator_kind == "file":
            self.index_file(
                workspace_id=str(context["workspace_id"]),
                path=canonical,
                run_id=run_id,
                operation_id=str(operation.get("id") or "") or None,
                opened=capability == "desktop.file.open.v1",
                supplied_hash=str(result.get("sha256") or "") or None,
            )

    def upsert_locator(
        self,
        *,
        workspace_id: str,
        kind: str,
        canonical_value: str,
        display_value: str,
        label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_locators(
                       id,workspace_id,kind,canonical_value,display_value,label,metadata,
                       last_used_at,use_count
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,now(),1)
                   ON CONFLICT (workspace_id,kind,canonical_value) DO UPDATE SET
                     display_value=EXCLUDED.display_value,
                     label=COALESCE(EXCLUDED.label,lh_locators.label),
                     metadata=lh_locators.metadata || EXCLUDED.metadata,
                     last_used_at=now(),use_count=lh_locators.use_count+1,
                     updated_at=now()
                   RETURNING *""",
                (str(uuid4()), workspace_id, kind, canonical_value, display_value, label, _json(metadata or {})),
            ).fetchone()
        return dict(row)

    def index_file(
        self,
        *,
        workspace_id: str,
        path: str,
        run_id: str | None = None,
        operation_id: str | None = None,
        opened: bool = False,
        supplied_hash: str | None = None,
    ) -> dict[str, Any] | None:
        file_path = Path(path).expanduser()
        if file_path.is_symlink():
            return None
        try:
            canonical = file_path.resolve(strict=True)
        except (FileNotFoundError, OSError):
            return None
        if not canonical.is_file():
            return None
        stat = canonical.stat()
        extension = canonical.suffix.lower()
        mime_type = mimetypes.guess_type(str(canonical))[0] or "application/octet-stream"
        content_hash = supplied_hash or self._file_hash(canonical)
        search_text = self._read_search_text(canonical, stat.st_size, extension)
        locator = self.upsert_locator(
            workspace_id=workspace_id,
            kind="file",
            canonical_value=str(canonical),
            display_value=str(canonical),
            label=canonical.name,
            metadata={"mime_type": mime_type},
        )
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_files(
                       id,workspace_id,locator_id,canonical_path,relative_path,name,
                       extension,mime_type,size_bytes,modified_at,content_hash,search_text,
                       active,last_seen_at,last_opened_at,last_modified_run_id
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s),%s,%s,TRUE,now(),%s,%s)
                   ON CONFLICT (workspace_id,canonical_path) DO UPDATE SET
                     locator_id=EXCLUDED.locator_id,name=EXCLUDED.name,extension=EXCLUDED.extension,
                     mime_type=EXCLUDED.mime_type,size_bytes=EXCLUDED.size_bytes,
                     modified_at=EXCLUDED.modified_at,content_hash=EXCLUDED.content_hash,
                     search_text=EXCLUDED.search_text,active=TRUE,last_seen_at=now(),
                     last_opened_at=CASE WHEN %s THEN now() ELSE lh_files.last_opened_at END,
                     last_modified_run_id=COALESCE(EXCLUDED.last_modified_run_id,lh_files.last_modified_run_id),
                     updated_at=now()
                   RETURNING *""",
                (
                    str(uuid4()), workspace_id, locator["id"], str(canonical), canonical.name,
                    canonical.name, extension, mime_type, stat.st_size, stat.st_mtime,
                    content_hash, search_text, _utc_now() if opened else None, run_id, opened,
                ),
            ).fetchone()
            if row and content_hash:
                connection.execute(
                    """INSERT INTO lh_file_revisions(
                           file_id,run_id,operation_id,content_hash,size_bytes,modified_at
                       ) VALUES (%s,%s,%s,%s,%s,to_timestamp(%s))
                       ON CONFLICT (file_id,content_hash) DO NOTHING""",
                    (row["id"], run_id, operation_id, content_hash, stat.st_size, stat.st_mtime),
                )
        return dict(row) if row else None

    def scan_workspace(self, *, workspace_id: str, roots: Iterable[str], max_files: int = 5000) -> dict[str, Any]:
        maximum = max(1, min(int(max_files), 20000))
        indexed = 0
        directories_indexed = 0
        skipped = 0
        for root_value in roots:
            root = Path(str(root_value)).expanduser().resolve()
            if not root.exists() or not root.is_dir() or root.is_symlink():
                continue
            for current, directories, filenames in os.walk(root):
                current_path = Path(current)
                relative_depth = len(current_path.relative_to(root).parts)
                self.upsert_locator(
                    workspace_id=workspace_id,
                    kind="directory",
                    canonical_value=str(current_path.resolve()),
                    display_value=str(current_path.resolve()),
                    label=current_path.name or str(current_path),
                    metadata={"indexed": True},
                )
                directories_indexed += 1
                directories[:] = [
                    name for name in directories
                    if name not in _SKIP_DIRECTORIES and not name.startswith(".") and relative_depth < 8
                ]
                for filename in filenames:
                    if indexed >= maximum:
                        return {
                            "workspace_id": workspace_id,
                            "indexed": indexed,
                            "directories_indexed": directories_indexed,
                            "skipped": skipped,
                            "truncated": True,
                        }
                    path = current_path / filename
                    try:
                        value = self.index_file(workspace_id=workspace_id, path=str(path))
                    except (OSError, PermissionError):
                        value = None
                    if value:
                        indexed += 1
                    else:
                        skipped += 1
        return {
            "workspace_id": workspace_id,
            "indexed": indexed,
            "directories_indexed": directories_indexed,
            "skipped": skipped,
            "truncated": False,
        }

    def search_files(self, *, workspace_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        maximum = max(1, min(int(limit), 100))
        terms = _tokens(query)
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

    def context(
        self,
        *,
        workspace_id: str,
        actor: str,
        conversation_id: str | None,
        query: str,
        message_limit: int = 20,
        file_limit: int = 20,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            conversation = None
            if conversation_id:
                conversation = connection.execute(
                    "SELECT * FROM lh_conversations WHERE id=%s AND workspace_id=%s AND actor=%s",
                    (conversation_id, workspace_id, actor),
                ).fetchone()
            messages = []
            if conversation:
                messages = connection.execute(
                    """SELECT role,content,run_id,metadata,created_at FROM lh_messages
                       WHERE conversation_id=%s ORDER BY id DESC LIMIT %s""",
                    (conversation["id"], max(1, min(message_limit, 50))),
                ).fetchall()
                messages = list(reversed(messages))
            active_task = None
            if conversation and conversation.get("active_task_id"):
                active_task = connection.execute(
                    """SELECT t.*,l.kind AS subject_kind,l.canonical_value AS subject,
                              l.display_value AS subject_display
                       FROM lh_memory_tasks t LEFT JOIN lh_locators l ON l.id=t.subject_locator_id
                       WHERE t.id=%s""",
                    (conversation["active_task_id"],),
                ).fetchone()
            recent_tasks = connection.execute(
                """SELECT t.id,t.goal,t.status,t.summary,t.updated_at,
                          l.kind AS subject_kind,l.canonical_value AS subject,
                          l.display_value AS subject_display
                   FROM lh_memory_tasks t LEFT JOIN lh_locators l ON l.id=t.subject_locator_id
                   WHERE t.workspace_id=%s AND t.actor=%s
                   ORDER BY (t.status='active') DESC,t.updated_at DESC LIMIT 12""",
                (workspace_id, actor),
            ).fetchall()
            locators = connection.execute(
                """SELECT id,kind,canonical_value,display_value,label,metadata,last_used_at,use_count
                   FROM lh_locators WHERE workspace_id=%s
                   ORDER BY last_used_at DESC NULLS LAST,use_count DESC LIMIT 30""",
                (workspace_id,),
            ).fetchall()
        return {
            "available": True,
            "conversation": self._conversation_dict(conversation) if conversation else None,
            "active_task": dict(active_task) if active_task else None,
            "recent_messages": [dict(row) for row in messages],
            "recent_tasks": [dict(row) for row in recent_tasks],
            "relevant_files": self.search_files(workspace_id=workspace_id, query=query, limit=file_limit),
            "recent_locators": [dict(row) for row in locators],
        }

    @staticmethod
    def _canonical_locator(kind: str, value: str) -> str:
        if kind in {"file", "directory"}:
            return str(Path(value).expanduser().resolve())
        return value.strip()

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _read_search_text(path: Path, size: int, extension: str) -> str:
        if extension not in _TEXT_EXTENSIONS or size > 1_000_000:
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:250_000]
        except OSError:
            return ""

    @staticmethod
    def _conversation_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "workspace_id": str(row["workspace_id"]),
            "actor": row["actor"],
            "title": row.get("title"),
            "status": row["status"],
            "active_task_id": str(row["active_task_id"]) if row.get("active_task_id") else None,
            "active_subject_kind": row.get("active_subject_kind"),
            "active_subject_value": row.get("active_subject_value"),
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
            "last_message_at": row["last_message_at"].isoformat() if row.get("last_message_at") else None,
        }

    @staticmethod
    def _file_dict(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["id"] = str(value["id"])
        for key in ("modified_at", "last_opened_at", "last_seen_at"):
            if value.get(key):
                value[key] = value[key].isoformat()
        return value
