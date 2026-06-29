from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from authlib.integrations.base_client import OAuthError
from fastapi import FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.auth import build_session_user, create_authentik_client
from app.config import Settings, get_settings
from app.litellm_client import LiteLLMAPIError, LiteLLMClient

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

settings = get_settings()
app = FastAPI(title="KISZ Auth Wrapper")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.session_https_only,
)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def get_client() -> LiteLLMClient:
    return LiteLLMClient(
        base_url=settings.normalized_litellm_base_url,
        master_key=settings.litellm_master_key,
    )


def _get_authenticated_user(request: Request) -> dict[str, Any] | None:
    user = request.session.get("user")
    return user if isinstance(user, dict) else None


def _require_authenticated_user(request: Request) -> dict[str, Any]:
    user = _get_authenticated_user(request)
    if user is None:
        raise PermissionError("Authentication required")
    return user


def _ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def _verify_csrf(request: Request, submitted_token: str) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(expected, submitted_token):
        raise PermissionError("Invalid CSRF token")


def _set_flash(request: Request, kind: str, message: str) -> None:
    request.session["flash"] = {"kind": kind, "message": message}


def _pop_flash(request: Request) -> dict[str, str] | None:
    flash = request.session.pop("flash", None)
    return flash if isinstance(flash, dict) else None


def _pop_generated_key(request: Request) -> str | None:
    generated_key = request.session.pop("generated_key", None)
    return generated_key if isinstance(generated_key, str) else None


def _format_timestamp(value: Any) -> str:
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def _prepare_keys(keys: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for key in keys:
        token_value = key.get("token") or key.get("key")
        prepared.append(
            {
                "display_name": key.get("key_alias")
                or key.get("token_id")
                or ((str(token_value)[:12] + "...") if token_value else "Unnamed key"),
                "delete_value": token_value,
                "spend": key.get("spend", 0),
                "max_budget": key.get("max_budget"),
                "created_at": _format_timestamp(key.get("created_at")),
                "updated_at": _format_timestamp(key.get("updated_at")),
                "raw": key,
            }
        )
    return prepared


async def _render_dashboard(
    request: Request,
    *,
    client: LiteLLMClient,
    error_message: str | None = None,
) -> HTMLResponse:
    try:
        user = _require_authenticated_user(request)
    except PermissionError:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    user_info: dict[str, Any] | None = None
    keys: list[dict[str, Any]] = []
    data_error = error_message

    try:
        payload = await client.get_user_info(user["user_id"])
        loaded_user_info = payload.get("user_info")
        user_info = loaded_user_info if isinstance(loaded_user_info, dict) else {}
        loaded_keys = payload.get("keys")
        if isinstance(loaded_keys, list):
            keys = [item for item in loaded_keys if isinstance(item, dict)]
    except LiteLLMAPIError as exc:
        data_error = exc.detail

    context = {
        "request": request,
        "settings": settings,
        "user": user,
        "user_info": user_info or {},
        "keys": _prepare_keys(keys),
        "csrf_token": _ensure_csrf_token(request),
        "flash": _pop_flash(request),
        "generated_key": _pop_generated_key(request),
        "error_message": data_error,
    }
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if _get_authenticated_user(request):
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/login", response_class=HTMLResponse)
async def login(request: Request):
    if _get_authenticated_user(request):
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    if request.query_params.get("start") == "1":
        authentik = create_authentik_client(settings)
        return await authentik.authorize_redirect(request, settings.authentik_redirect_uri)

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "flash": _pop_flash(request),
        },
    )


@app.get("/callback")
async def callback(request: Request):
    authentik = create_authentik_client(settings)
    client = get_client()

    try:
        token = await authentik.authorize_access_token(request)
        claims = token.get("userinfo")
        if claims is None:
            claims = await authentik.userinfo(token=token)
        if not isinstance(claims, dict):
            raise ValueError("Missing OIDC userinfo payload")
    except (OAuthError, ValueError) as exc:
        _set_flash(request, "error", f"Authentication failed: {exc}")
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    session_user = build_session_user(claims, settings)
    try:
        await client.ensure_user(
            user_id=session_user["user_id"],
            user_email=session_user.get("email"),
            user_role=session_user["role"],
            max_budget=settings.default_user_budget,
        )
    except LiteLLMAPIError as exc:
        _set_flash(request, "error", f"LiteLLM sync failed: {exc.detail}")
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    request.session["user"] = session_user
    _ensure_csrf_token(request)
    _set_flash(request, "success", "Signed in successfully.")
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return await _render_dashboard(request, client=get_client())


@app.post("/keys/create")
async def create_key(
    request: Request,
    csrf_token: str = Form(...),
    key_alias: str = Form(default=""),
    max_budget: str = Form(default=""),
):
    try:
        user = _require_authenticated_user(request)
        _verify_csrf(request, csrf_token)
    except PermissionError as exc:
        _set_flash(request, "error", str(exc))
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    budget_value = None
    if max_budget.strip():
        try:
            budget_value = float(max_budget.strip())
        except ValueError:
            return await _render_dashboard(
                request,
                client=get_client(),
                error_message="Key budget must be a valid number.",
            )

    try:
        response = await get_client().generate_key(
            user_id=user["user_id"],
            key_alias=key_alias.strip() or None,
            max_budget=budget_value,
        )
    except LiteLLMAPIError as exc:
        return await _render_dashboard(
            request,
            client=get_client(),
            error_message=f"Key creation failed: {exc.detail}",
        )

    request.session["generated_key"] = response.get("key") or response.get("token")
    _set_flash(request, "success", "Key created successfully.")
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/keys/delete")
async def delete_key(
    request: Request,
    csrf_token: str = Form(...),
    key: str = Form(...),
):
    try:
        user = _require_authenticated_user(request)
        _verify_csrf(request, csrf_token)
    except PermissionError as exc:
        _set_flash(request, "error", str(exc))
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    client = get_client()
    try:
        payload = await client.get_user_info(user["user_id"])
        loaded_keys = payload.get("keys")
        keys = loaded_keys if isinstance(loaded_keys, list) else []
    except LiteLLMAPIError as exc:
        return await _render_dashboard(
            request,
            client=client,
            error_message=f"Failed to load keys: {exc.detail}",
        )

    owned_values = {
        str(item.get("token") or item.get("key"))
        for item in keys
        if isinstance(item, dict) and (item.get("token") or item.get("key"))
    }
    if key not in owned_values:
        return await _render_dashboard(
            request,
            client=client,
            error_message="You can only delete keys owned by the logged-in user.",
        )

    try:
        await client.delete_key(key)
    except LiteLLMAPIError as exc:
        return await _render_dashboard(
            request,
            client=client,
            error_message=f"Key deletion failed: {exc.detail}",
        )

    _set_flash(request, "success", "Key deleted successfully.")
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
