import os

from fastapi import Request
from fastapi.responses import RedirectResponse


def _favicon_target(request: Request) -> str:
    server_root_path = os.getenv("SERVER_ROOT_PATH", "")
    prefix = server_root_path.rstrip("/") if server_root_path and server_root_path != "/" else ""
    target = f"{prefix}/ui/favicon-v2.ico"
    return f"{target}?{request.url.query}" if request.url.query else target


def register_routes() -> None:
    """Add the fork's own routes to the running proxy app.

    Registering from here rather than patching proxy_server.py keeps that
    17k-line file, which upstream changes constantly, out of
    aihpi/authentik/.
    """
    from litellm.proxy.proxy_server import app

    if any(getattr(route, "path", None) == "/favicon.ico" for route in app.routes):
        return

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon_redirect(request: Request):  # pyright: ignore[reportUnusedFunction]  # registered by decorator
        return RedirectResponse(url=_favicon_target(request))
