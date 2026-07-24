from __future__ import annotations

from .models import Capability, ConfirmationMode, KernelMode, Risk


PROJECT_FILE_WRITE_CAPABILITY = Capability(
    tool_name="system.file.write.v1",
    command="file write",
    description="Atomically create or replace one UTF-8 file inside the local project root",
    kernel=KernelMode.SYSTEM,
    executor="project_file",
    operation="file_write",
    risk=Risk.HIGH,
    confirmation=ConfirmationMode.EXPLICIT,
    writes=True,
    aliases=(
        "create file",
        "write file",
        "create html",
        "創建文件",
        "製作 html",
        "建立網頁",
    ),
    arguments={
        "path": {"type": "string", "required": True},
        "content": {"type": "string", "required": True},
        "overwrite": {"type": "boolean", "required": False},
    },
)
