from __future__ import annotations

from .api import create_app
from .config import Settings


def main() -> None:
    import uvicorn
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
