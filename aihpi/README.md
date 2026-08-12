# AIHPI Fork

Fork-specific code for [aihpi/tool-litellm](https://github.com/aihpi/tool-litellm). All customizations live in this directory to avoid modifying upstream files and prevent merge conflicts.

## Local Development

Apply the Authentik SSO and HPI branding patches before running the proxy:

```bash
bash aihpi/branding/apply.sh
```

Then start the proxy:

```bash
python litellm/proxy/proxy_cli.py --config litellm/proxy/dev_config.yaml --detailed_debug --reload
```

The proxy runs at http://localhost:4000 with the UI at http://localhost:4000/ui/

Register the AIHPI custom provider by setting this env var before starting:

```bash
export LITELLM_WORKER_STARTUP_HOOKS=aihpi:register
```

## Docker Build

Build the Docker image locally:

```bash
docker build -t litellm-aihpi .
```

The `build-and-publish.yaml` workflow runs `aihpi/branding/apply.sh` before `docker build`, so branding and Authentik patches are applied automatically in CI.

Run the container:

```bash
docker run -p 4000:4000 \
  -e LITELLM_WORKER_STARTUP_HOOKS=aihpi:register \
  -e LITELLM_MASTER_KEY=sk-1234 \
  -v $(pwd)/litellm/proxy/dev_config.yaml:/app/config.yaml \
  litellm-aihpi \
  --config /app/config.yaml
```

## Structure

```
aihpi/
  __init__.py          # Startup hook: register()
  provider.py          # CustomLLM subclass (embedding + image_edit)
  authentik/           # Fork versions of upstream files with Authentik SSO
  branding/
    apply.sh           # Patches branding + Authentik into source tree
    hpi-theme.css      # HPI orange CSS variable overrides
    LoginPage.tsx       # Login page with Authentik button
    layout.tsx          # Dashboard layout with legal footer
```

## How Upstream Sync Works

The nightly CI workflow (`rebase-upstream.yml`) merges `upstream/litellm_internal_staging` into `aihpi-provider`. Since no upstream files are modified in git, the merge is conflict-free.

If upstream changes one of the Authentik files significantly, the Docker build will fail. To fix: update the corresponding file in `aihpi/authentik/` to incorporate upstream's changes alongside the Authentik additions.
