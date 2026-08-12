# AIHPI Fork

Fork-specific code for [aihpi/tool-litellm](https://github.com/aihpi/tool-litellm), a fork of
[BerriAI/litellm](https://github.com/BerriAI/litellm).

## The one rule

**Never commit a change to a file that exists upstream.** That is what makes the nightly upstream
merge conflict-free. Everything the fork needs is either a new file, or a patch applied at build
time by `branding/apply.sh`.

To check you have not broken this:

```bash
git fetch upstream litellm_internal_staging
git diff upstream/litellm_internal_staging --name-only -- litellm/ ui/ | grep -v "_experimental/out"
```

Every path listed must be a file that does **not** exist upstream. Today that is the legal
components, the HPI logos and favicons, `uiAssetPath.ts`, and `custom_authentik_sso.py`.

## How the patches get applied

`branding/apply.sh` runs as a step in `.github/workflows/build-and-publish.yaml`, **before**
`docker build`. It is not in the `Dockerfile`, because the `Dockerfile` is an upstream file and
editing it would reintroduce the merge conflict we are avoiding.

Consequence: **a bare `docker build .` produces an unbranded image with no Authentik.** There is no
error, it just silently lacks the fork's changes. Always build with:

```bash
bash aihpi/branding/apply.sh && docker build -t litellm-aihpi:local .
```

`apply.sh` is idempotent, so re-running it is safe.

## Local development

`apply.sh` edits real files under `litellm/` and `ui/`. In CI that tree is throwaway; locally it
leaves you with dirty files that look like your own edits. Revert them when you are done:

```bash
git checkout -- litellm/ ui/
```

`litellm/aihpi/` is gitignored, but the patched upstream files are **not**. Check `git status`
before committing so a patched file never lands in a commit.

Running the whole thing locally, with a database:

```bash
docker network create litellm-net

docker run -d --name litellm-pg --network litellm-net \
  -e POSTGRES_USER=llm -e POSTGRES_PASSWORD=llm -e POSTGRES_DB=litellm postgres:16-alpine

bash aihpi/branding/apply.sh && docker build -t litellm-aihpi:local .

docker run -d --name litellm-aihpi-test --network litellm-net -p 4000:4000 \
  -e LITELLM_WORKER_STARTUP_HOOKS=litellm.aihpi:register \
  -e LITELLM_MASTER_KEY=sk-1234 \
  -e DATABASE_URL=postgresql://llm:llm@litellm-pg:5432/litellm \
  litellm-aihpi:local --port 4000
```

The UI is at http://localhost:4000/ui/ (login `admin` / `sk-1234`). Note that `/ui/` redirects a
logged-out user to `/sso/key/generate`, a legacy server-rendered page that is **not** branded; the
branded login page is `/ui/login`.

Teardown:

```bash
docker rm -f litellm-aihpi-test litellm-pg && docker network rm litellm-net
```

## Contents

```
aihpi/
  __init__.py                   register() -- the proxy startup hook
  provider.py                   CustomLLM subclass: embedding + image_edit
  authentik/                    fork copies of 7 upstream files carrying Authentik SSO
    manifest.txt                which copy replaces which upstream path
    baseline.sha256             hash of upstream's version each copy was taken from
    rebaseline.sh               re-record baselines after re-syncing a copy
  branding/
    apply.sh                    applies everything below, plus authentik/
    hpi-theme.css               HPI palette, substituted into globals.css @theme vars
    layout.tsx                  dashboard layout with the HPI banner and legal footer
    provider_create_field.json  AIHPI entry for the UI's add-model provider list
```

## The AIHPI provider

Registered at runtime through litellm's own `custom_provider_map`, so no core file needs to know it
exists. `apply.sh` copies the package to `litellm/aihpi/` because the built wheel only ships the
`litellm` package, so a top-level `aihpi` module is not importable at runtime.

Enable it with:

```
LITELLM_WORKER_STARTUP_HOOKS=litellm.aihpi:register
```

Note the `litellm.` prefix. A bare `aihpi:register` fails inside the image.

## When the Docker build fails after an upstream merge

This is the designed failure mode. The merge stays clean and the build tells you what moved.

`apply.sh` finds its edit sites by matching on exact strings. If upstream changes one, the script
exits with the file name and the message *"upstream changed X; update aihpi/branding/apply.sh"*.
The anchors are:

| File | Anchor |
|---|---|
| `src/contexts/AntdGlobalProvider.tsx` | `theme={{ cssVar: true }}` |
| `src/app/login/LoginPage.tsx` | `<Title level={2}>🚅 LiteLLM</Title>` |
| `src/components/leftnav.tsx` | the `<Link ... aria-label="LiteLLM home">` logo block |
| `src/app/layout.tsx` | the `export const metadata: Metadata = {...}` block |
| `src/app/globals.css` | the `--color-tremor-brand*` declarations |

Fix by opening the file, finding where the code moved, and updating the anchor in `apply.sh`.

Upstream may also change a component's props so that `branding/layout.tsx` (a whole-file copy) no
longer type-checks. The build fails with a TypeScript error naming the prop. Fix by diffing our copy
against upstream's current version and re-applying the HPI additions (the `LegalBanner` and
`LegalFooter` lines) on top.

### Stale Authentik copies

The 7 files in `authentik/` are whole-file copies, so `apply.sh` overwriting upstream's version would
silently discard any change upstream made to it. `proxy_server.py` alone is 17k lines and upstream
touches it constantly, so this is the one place the fork could lose an upstream fix without anyone
noticing.

`apply.sh` guards it. `baseline.sha256` records the hash of upstream's version each copy was taken
from, and every file is checked before being copied:

- hash matches our copy: already applied, skip
- hash matches the baseline: safe, apply
- neither: **build fails**, naming the file

To resolve, for each file named:

1. `diff aihpi/authentik/<copy> <the upstream path from manifest.txt>` to see both our additions and
   upstream's new work
2. re-apply the Authentik additions on top of upstream's current version
3. from a pristine tree (`git checkout -- litellm/ ui/`), run `bash aihpi/authentik/rebaseline.sh`

`rebaseline.sh` refuses to run on a dirty tree, because baselining a patched tree would record our
own output as the baseline and disable the guard permanently.

## Why branding is applied where it is

Three independent theming systems, which is why one CSS override was not enough:

- **antd** emits its own `--ant-color-primary` scoped to a generated class, which outranks a `:root`
  rule, and derives hover/active/border from the token. So the colour goes through
  `ConfigProvider`, not CSS.
- **Tremor** reads `@theme` variables in `globals.css`, and Tailwind v4 bakes utilities from those
  values at build time. So the declarations are rewritten in place, not overridden later.
- **Static assets.** The nav logo is served by `/get_image`, so `apply.sh` overwrites
  `litellm/proxy/logo.jpg` (the endpoint sniffs the file header, so a PNG under that name is fine).
  The tab icon needs `src/app/favicon.ico` replaced, because Next.js auto-emits a `<link>` from that
  file *ahead* of anything declared in `metadata`.
