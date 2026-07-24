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


DIRECTORY_CREATE_CAPABILITY = Capability(
    tool_name="system.directory.create.v1",
    command="directory create",
    description="Create one confined project directory without invoking an interactive shell",
    kernel=KernelMode.SYSTEM,
    executor="project_file",
    operation="directory_create",
    risk=Risk.NORMAL,
    confirmation=ConfirmationMode.EXPLICIT,
    writes=True,
    aliases=("mkdir", "create directory", "create folder", "建立目錄", "創建文件夾"),
    arguments={
        "path": {"type": "string", "required": True},
        "parents": {"type": "boolean", "required": False},
    },
)


SYSTEM_TYPED_CAPABILITIES = (
    PROJECT_FILE_WRITE_CAPABILITY,
    DIRECTORY_CREATE_CAPABILITY,
)
