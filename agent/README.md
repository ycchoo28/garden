# The garden agent package

This folder holds the `garden` command-line tool, the MCP server it runs, the skills that teach agents to use it, and the installers that put it on a laptop.
It is a separate Python project.
It never imports the portal backend, and it is not part of the server image.

## What is in here

- `src/garden/`: the package.
  - `cli/` routes built-in commands and compiles the contract into command flags.
  - `engine/` owns HTTP calls, contract caching, tool discovery and confirmation waits.
  - `auth/` owns browser login, PKCE, the loopback listener and credential storage.
  - `dsh/` owns model configuration, offline fallback, launchers and process execution.
  - `mcp/` serves tools over stdio.
  - `shared/` holds settings, errors, JSON guards and terminal output.
  - `skills/` holds the `SKILL.md` files that ship inside the package.
- `tests/`: the process-level tests, run against `fake_portal.py`, a small stand-in for the portal.
- `install/install.sh` and `install/install.ps1`: the installers.
- `install/dsh/cordis.patch.yml`: the template for dsh's home patch. `garden dsh-config` fills it in from the portal.
- `scripts/build_zipapp.py`: builds `dist/garden.pyz`, the one file the portal serves.
- `scripts/build_dsh_bundle.py`: builds the dsh bundles from `install/dsh/package.json` and its lockfile.

## Commands

Run these from the repository root.

- `just agent-check`: format, lint, strict types and package import boundaries.
  `just check` runs it, and `just test` runs the agent tests.
  Architecture tests enforce public subpackage imports and explicit exports.
- `just agent-build`: builds `agent/dist/garden.pyz`.
- `just agent-install-test`: runs the installer unattended against the fake portal. It needs no network.
- `just agent-dsh-bundle`: builds both dsh bundles. It needs Node.js, npm and network access to the npm registry.
- `just agent-publish`: builds the zipapp, reuses any dsh bundle already built, and copies the files into `GARDEN_AGENT_DOWNLOADS_DIR`, which the portal serves. The default is `backend/var/agent-downloads`.

Inside `agent/`, `uv run pytest tests` runs the tests on their own.

## Commands the person sees

- `garden login`, `garden whoami`, `garden logout`: sign in through the browser, check the login, and end this device's login.
- `garden tools`: list the tools this person may use. `--json` gives the raw list.
- `garden mcp`: the MCP server over stdio.
- `garden skills install --to <folder>`: copies the skills for agents that read them.
- `garden dsh [args]`: runs dsh with the portal's model and the `garden` MCP server.
- `garden dsh-config`: writes dsh's model patch from the portal's default model, then stops.
- `garden dsh-launcher --to <path> --garden <path>`: writes the `dsh` launcher. The installers run it once.

## Sign-in and where secrets go

- `garden login` opens the browser, or prints the link with `--no-open`.
- The login is stored in the OS keychain (`keyring`), under the service `garden` and the portal address.
- `GARDEN_SECRET_STORE=file:<path>` replaces the keychain with a plain JSON file.
  It prints a warning, and it exists for tests only.
- No token is written into the repository, the installer, or the dsh patch.
  The dsh patch names the environment variable that holds the login (`GARDEN_AGENT_MODEL_TOKEN`), and `garden dsh` sets it for dsh's process only.

## The model

- The model is the portal's.
  dsh calls `/api/agent/model/v1` with the person's CLI login, and the portal calls the upstream model with its own key.
  No laptop gets the upstream key or address.
- `GET /api/agent/model/v1/models` returns the models the gateway forwards, and a top-level `default` that names the one dsh uses.
  `default` is null when no model is allowed.
- The patch names one provider, `garden-portal`, whose `baseURL` is `<portal>/api/agent/model/v1`.
- Each `garden dsh` start refreshes the patch from the portal.
  When the portal cannot be reached, the last patch stays and a warning says so.
  A refusal from the portal stops dsh and asks the person to run `garden login`.

## Configuration

- `GARDEN_API_URL` is the portal's address. The installer writes it into `config.json`, so `garden` runs without it afterwards.
- `GARDEN_HOME` is the folder for the CLI's files, and it defaults to `~/.garden`.
- `GARDEN_DSH_HOME` is dsh's home, and it defaults to `~/.dsh`.

## Packaging and installers

- The runtime is the standard library plus two pinned dependencies: `keyring` for the keychain and `pyyaml` for the dsh config.
- `just agent-build` writes `dist/garden.pyz`, a zipapp that vendors those dependencies.
- The installer reads the portal's manifest at `GET /api/agent/manifest`, which needs no sign-in.
  It fetches `garden.pyz`, the dsh bundle for its platform and `dsh-config.yml`, and checks each SHA-256 before it installs anything.
- `install.sh` needs only curl, and it gets Python 3.12 through uv when no suitable Python is present.
  `GARDEN_PYTHON` names another Python 3.12 to use instead.
- `install.ps1` does the same for Windows PowerShell 5.1 and PowerShell 7, with `tar.exe` for the bundle.
- Sign-in is the last step.
  If it fails, the CLI, dsh and the skills stay installed, and the message names `garden login` as the one command to retry.
- Node.js runs dsh.
  Without it the installer still finishes, and it says so.

To install from a portal, the one-liners set the portal's address first, as the Get started page shows.

macOS and Linux:

```
curl -fsSL https://<portal>/api/agent/downloads/install.sh | GARDEN_API_URL=https://<portal> bash
```

Windows PowerShell:

```
$env:GARDEN_API_URL = 'https://<portal>'; irm https://<portal>/api/agent/downloads/install.ps1 | iex
```

## Tests

- `test_dsh.py`: the launcher text, the patch contents, the default model following the portal, a portal that cannot be reached keeping the last patch, and a refusal stopping dsh.
- `test_install.py`: a zipapp built from the source and a stand-in dsh bundle, served with the manifest, installed unattended, then signed in and checked. It also checks the text of both installers, since `pwsh` is not part of CI.

The installer tests build the zipapp from the source under the test interpreter, so they need no network.
The full vendored build (`just agent-build`) needs the package index for its Windows pass.

## Not covered by the tests

- `install.ps1` is not run by the tests. A Windows machine needs a hand run before a release.
- The dsh bundles are large, and they have not been slimmed.
- The format of dsh's `.credentials.yaml` is not verified.
