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
  routes.py                     fork's own FastAPI routes, added at startup
  manifest.txt                  the whole-file copies and what they replace
  baseline.sha256               hash + git blob id of upstream's version per copy
  rebaseline.sh                 re-record baselines after re-syncing a copy
  authentik/ui_sso.py           copy: the Authentik SSO handler
  branding/
    apply.sh                    applies every patch, then the copies above
    layout.tsx                  copy: dashboard shell with the HPI banner and footer
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
| `src/app/login/LoginPage.tsx` | `<h2 className="text-3xl font-semibold text-foreground">🚅 LiteLLM</h2>` |
| `src/components/leftnav.tsx` | the `<Link ... aria-label="LiteLLM home">` logo block |
| `src/app/layout.tsx` | the `export const metadata: Metadata = {...}` block |
| `src/app/globals.css` | the `--primary` / `--primary-foreground` declarations |
| `discovery_endpoints/ui_discovery_endpoints.py` | `sso_configured: Final = has_user_setup_sso()` |
| `health_endpoints/_health_endpoints.py` | the `prompt="test from litellm"` pair in `ahealth_check` |

Fix by opening the file, finding where the code moved, and updating the anchor in `apply.sh`.

`branding/layout.tsx` is the other whole-file copy, listed in `manifest.txt` alongside `ui_sso.py`
and covered by the same guard and nightly re-sync.

### Stale whole-file copies

A whole-file copy goes stale on *any* upstream change to that file, while an anchored patch only
trips when the lines it targets move. Upstream commits to the files this fork touches ran at roughly
one a day, so the copies were failing the build far more often than our own additions warranted.
Two files still need to be copies. `ui_sso.py` has 225 changed lines across 24 hunks, mostly one-line
`authentik_client_id` threading, where twenty-two anchors would be more fragile than one copy.
`layout.tsx` restructures the app shell into a column so the legal footer is a real row rather than an
overlay. Everything else is a patch in `branding/apply.sh`.

Prefer adding over patching, and patching over copying. `proxy_server.py` used to be copied here,
17k lines that upstream touches in nearly every commit, and it existed for exactly one route. That
route now lives in `routes.py` and is registered from the startup hook, so the file is untouched. If
a future addition is a route, a callback, or anything registerable at runtime, move it out the same
way instead of growing this directory.

`apply.sh` guards the copies. `baseline.sha256` records the sha256 and the git blob id of upstream's
version the copy was taken from, and the file is checked before being overwritten:

- hash matches our copy: already applied, skip
- hash matches the baseline: safe, apply
- neither: **build fails**, naming the file

In practice you should rarely see that failure, because the merge workflow repairs it first. Its
`Re-sync whole-file copies onto upstream` step runs after the merge and before the push: it fetches
the baseline blob with `git cat-file`, 3-way merges our additions onto upstream's new version with
`git merge-file`, re-runs `rebaseline.sh` and commits the result. Deterministic, not the LLM
resolver used for real merge conflicts, because the base is exact and this is auth code. A genuine
overlap fails the step, so nothing is pushed and the branch stays buildable.

To resolve one by hand:

1. `diff aihpi/<copy> <the upstream path from manifest.txt>` to see both our additions and
   upstream's new work
2. re-apply the fork additions on top of upstream's current version
3. from a pristine tree (`git checkout -- litellm/ ui/`), run `bash aihpi/rebaseline.sh`

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
