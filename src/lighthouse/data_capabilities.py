from __future__ import annotations

from .models import Capability, ConfirmationMode, KernelMode, Risk


def _cap(tool_name: str, command: str, description: str, operation: str, risk: Risk, confirmation: ConfirmationMode, writes: bool, *, aliases: tuple[str, ...] = (), arguments: dict | None = None) -> Capability:
    return Capability(tool_name=tool_name, command=command, description=description, kernel=KernelMode.DATA, executor="postgres", operation=operation, risk=risk, confirmation=confirmation, writes=writes, aliases=aliases, arguments=arguments or {})


DATA_KERNEL_CAPABILITIES: tuple[Capability, ...] = (
    _cap(
        "data.target.bind.v1", "data target bind",
        "Bind an additional PostgreSQL Data Target to the current workspace under a stable alias",
        "catalog_bind", Risk.HIGH, ConfirmationMode.EXPLICIT, True,
        aliases=("bind data target", "綁定數據庫", "連接業務資料庫"),
        arguments={"target_id": {"type": "string", "required": True}, "alias": {"type": "string", "required": True}, "is_default": {"type": "boolean", "required": False}},
    ),
    _cap(
        "data.catalog.sync.v1", "data catalog sync",
        "Inspect an authorized PostgreSQL target and persist its schema graph and resource catalog",
        "catalog_sync", Risk.NORMAL, ConfirmationMode.DIRECT, True,
        aliases=("schema graph sync", "同步數據目錄", "同步資料庫結構"),
        arguments={"target_alias": {"type": "string", "required": False}},
    ),
    _cap(
        "data.catalog.resources.v1", "data resources",
        "List cataloged PostgreSQL resources and their read/write policy surfaces",
        "catalog_resources", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("resource catalog", "數據資源目錄", "資料表資源"),
        arguments={"target_alias": {"type": "string", "required": False}, "limit": {"type": "integer", "required": False}},
    ),
    _cap(
        "data.resource.list.v1", "resource list",
        "List rows from a cataloged PostgreSQL resource using safe columns, filters and ordering",
        "resource_list", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("list resource", "列出資源", "資源列表"),
        arguments={"target_alias": {"type": "string", "required": False}, "resource": {"type": "string", "required": True}, "columns": {"type": "array", "required": False}, "filters": {"type": "object", "required": False}, "order_by": {"type": "array", "required": False}, "limit": {"type": "integer", "required": False}},
    ),
    _cap(
        "data.resource.show.v1", "resource show",
        "Read one cataloged resource record by its declared primary key",
        "resource_show", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("show resource", "查看資源", "查看記錄"),
        arguments={"target_alias": {"type": "string", "required": False}, "resource": {"type": "string", "required": True}, "key": {"required": True}, "columns": {"type": "array", "required": False}},
    ),
    _cap(
        "data.resource.search.v1", "resource search",
        "Search a cataloged resource through declarative filters without model-authored SQL",
        "resource_search", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("search resource", "搜索資源", "查找記錄"),
        arguments={"target_alias": {"type": "string", "required": False}, "resource": {"type": "string", "required": True}, "filters": {"type": "object", "required": False}, "columns": {"type": "array", "required": False}, "order_by": {"type": "array", "required": False}, "limit": {"type": "integer", "required": False}},
    ),
    _cap(
        "data.resource.update.v1", "resource update",
        "Update explicitly write-enabled columns on one cataloged resource record",
        "resource_update", Risk.HIGH, ConfirmationMode.EXPLICIT, True,
        aliases=("update resource", "更新資源", "修改記錄"),
        arguments={"target_alias": {"type": "string", "required": False}, "resource": {"type": "string", "required": True}, "key": {"required": True}, "changes": {"type": "object", "required": True}},
    ),
    _cap(
        "data.resource.policy.v1", "resource policy",
        "Set explicit writable columns and bounded query policy for one cataloged resource",
        "resource_policy", Risk.HIGH, ConfirmationMode.EXPLICIT, True,
        aliases=("configure resource policy", "配置資源權限"),
        arguments={"target_alias": {"type": "string", "required": False}, "resource": {"type": "string", "required": True}, "policy": {"type": "object", "required": True}},
    ),
    _cap(
        "data.semantic.register.v1", "data semantic register",
        "Register a declarative semantic command backed by an existing resource policy",
        "semantic_register", Risk.HIGH, ConfirmationMode.EXPLICIT, True,
        aliases=("register semantic command", "註冊語義指令", "建立業務查詢"),
        arguments={"target_alias": {"type": "string", "required": False}, "command": {"type": "string", "required": True}, "resource": {"type": "string", "required": True}, "action": {"type": "string", "required": False}, "definition": {"type": "object", "required": True}},
    ),
    _cap(
        "data.semantic.list.v1", "data semantic list",
        "List registered semantic commands for one PostgreSQL data world",
        "semantic_list", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("list semantic commands", "語義指令列表"),
        arguments={"target_alias": {"type": "string", "required": False}},
    ),
    _cap(
        "data.semantic.query.v1", "data semantic",
        "Execute a registered semantic data command backed by a resource policy, not raw SQL",
        "semantic_query", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("semantic data", "語義數據指令", "業務查詢"),
        arguments={"target_alias": {"type": "string", "required": False}, "command": {"type": "string", "required": True}, "params": {"type": "object", "required": False}, "limit": {"type": "integer", "required": False}},
    ),
)
