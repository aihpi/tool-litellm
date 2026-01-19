# Future Work / Roadmap

This file tracks follow-up items that are planned but not yet implemented.

## Auth & SSO
- Evaluate Authentik (OIDC preferred) to replace/augment LDAP login.
- Define admin mapping (group/role claim) and email claim mapping.
- Decide whether to keep local admin as break-glass fallback.
- Add temporary accounts with expiry (e.g., 7 days) for workshops.
- Add an access request flow (request → admin approval → account enabled with expiry).

## Feedback Collection
- Replace Zapier webhook with a self-hosted feedback endpoint.
- Decide storage: reuse existing Postgres vs separate DB.
- Add a basic admin view or export for collected feedback.

## GitOps / Deployments
- Install ArgoCD on the new RKE cluster.
- Create ArgoCD Application for `litellm-k8s` `overlays/prod`.
- Decide on Image Updater or manual tag bumps in Git.

## CI / Build
- Confirm rebase workflow + build trigger behavior after conflicts.
- Decide if build should run on rebase success only (explicit dispatch).

## UI / Branding
- Keep branding alignment across login, navbar, loading screen.
- Verify asset paths in production bundle (public assets copy).
- Confirm footer/legal placement after further UI changes.

## Ops / Observability
- Decide on log/metric stack for proxy + model services (Loki/Prometheus/Grafana).
- Define alerting rules (proxy health, model availability, error rate).
- Add Prometheus metrics export and Grafana dashboards for LiteLLM proxy usage.
