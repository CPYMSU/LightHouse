from __future__ import annotations

import json
from typing import Any

from . import __version__
from .api_v12 import create_app
from .config import Settings


class InstanceAwareApp:
    def __init__(self, app: Any, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/healthz":
            payload = json.dumps(
                {
                    "status": "ok",
                    "version": __version__,
                    "instance_id": self.settings.instance_id,
                    "instance_name": self.settings.instance_name,
                    "port": self.settings.port,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(payload)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return
        await self.app(scope, receive, send)


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    app = InstanceAwareApp(create_app(settings), settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
