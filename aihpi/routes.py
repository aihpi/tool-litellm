from fastapi import Request
from fastapi.responses import RedirectResponse


def register_routes() -> None:
    """Add the fork's own routes to the running proxy app.

    Registering from here rather than patching proxy_server.py keeps that
    17k-line file, which upstream changes constantly, out of
    aihpi/authentik/.
    """
    from litellm.proxy.proxy_server import app, server_root_path

    if any(getattr(route, "path", None) == "/favicon.ico" for route in app.routes):
        return

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon_redirect(request: Request):  # pyright: ignore[reportUnusedFunction]  # registered by decorator
        target = f"{server_root_path.rstrip('/')}/ui/favicon-v2.ico"
        return RedirectResponse(url=f"{target}?{request.url.query}" if request.url.query else target)
