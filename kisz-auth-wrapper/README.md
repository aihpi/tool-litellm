# KISZ Auth Wrapper

Small FastAPI service that sits beside LiteLLM and handles:

- Authentik OIDC sign-in
- automatic LiteLLM user provisioning via the admin API
- self-service LiteLLM key creation and deletion

The wrapper keeps the LiteLLM master key server-side only. Users authenticate to the wrapper, then use generated LiteLLM keys directly against the LiteLLM proxy.

## Phase 1 scope

- `/login` and `/callback` for Authentik OIDC
- `/dashboard` to show the authenticated user and their LiteLLM keys
- `/keys/create` to create a new key for the logged-in user
- `/keys/delete` to delete one of the logged-in user's keys
- `/logout` to clear the wrapper session

## Environment variables

```bash
AUTHENTIK_ISSUER=https://auth.example.com/application/o/kisz-llm
AUTHENTIK_CLIENT_ID=kisz-llm
AUTHENTIK_CLIENT_SECRET=super-secret
AUTHENTIK_REDIRECT_URI=https://llm-portal.example.com/callback

LITELLM_BASE_URL=http://litellm-service:4000
LITELLM_MASTER_KEY=sk-your-master-key

SESSION_SECRET=change-me
DEFAULT_USER_BUDGET=10.0
DEFAULT_USER_ROLE=internal_user
ADMIN_AUTHENTIK_GROUP=kisz-admins
AUTHENTIK_GROUPS_CLAIM=groups
SESSION_HTTPS_ONLY=true
```

## Authentik setup

1. Create an OAuth2/OpenID Provider in Authentik.
2. Use a confidential client.
3. Set the redirect URI to the wrapper callback URL, for example:

```text
https://llm-portal.example.com/callback
```

4. Create an Application that points at the provider.
5. Use the provider issuer URL as `AUTHENTIK_ISSUER`.

The wrapper assigns LiteLLM roles from Authentik group membership:

- if the authenticated user belongs to `ADMIN_AUTHENTIK_GROUP`, the wrapper provisions or updates them as `proxy_admin`
- otherwise the wrapper uses `DEFAULT_USER_ROLE`

## Local development

```bash
cd kisz-auth-wrapper
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Security notes

- The wrapper uses CSRF tokens on mutating forms.
- The wrapper verifies that a deleted key belongs to the current user before calling LiteLLM with the master key.
- Browser sessions are local to the wrapper and do not expose the LiteLLM master key.

## Container image

This service is intended to be built from the repository root with:

```bash
docker build -f kisz-auth-wrapper/Dockerfile -t ghcr.io/aihpi/tool-kisz-auth-wrapper:aihpi-provider .
```

A dedicated GitHub Actions workflow publishes the wrapper image separately from the main LiteLLM image.

## Kubernetes

Phase 1 Kubernetes deployment lives in the separate `litellm-k8s` repo.

- the wrapper image is built from this repo
- the staging-only Kustomize resources live in `litellm-k8s`
- the wrapper is not wired into the LiteLLM host/path routing yet

For multi-replica support in Phase 2, move session storage to Redis.
