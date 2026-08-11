# Recipe: Cut a Release

How to publish a new version of ytdl-stash.

---

## Quick Path (script)

```powershell
# Minor bump (default) — e.g. v0.13.0 → v0.14.0
.\scripts\release.ps1

# Patch bump — e.g. v0.14.0 → v0.14.1
.\scripts\release.ps1 -Bump patch

# Major bump — e.g. v0.14.1 → v1.0.0
.\scripts\release.ps1 -Bump major

# Explicit version
.\scripts\release.ps1 -Version 1.0.0

# Preview without making changes
.\scripts\release.ps1 -DryRun

# Tag + release current HEAD (no commit step)
.\scripts\release.ps1 -NoCommit
```

The script handles: commit → tag → push → GitHub release.
The CI workflow (`release.yml`) triggers on the tag push and builds the Docker image.

---

## Manual Path (step-by-step)

### 1. Decide the version

Look at the latest tag and pick the next one:

```powershell
git tag --sort=-creatordate | Select-Object -First 1
# e.g. v0.13.0  →  next is v0.14.0
```

### 2. Commit changes

```powershell
git add -A
git commit -m "Your commit message here"
```

### 3. Create and push the tag

```powershell
git tag v0.14.0
git push origin main
git push origin v0.14.0
```

### 4. Create the GitHub release

```powershell
gh release create v0.14.0 --title "v0.14.0" --generate-notes
```

Or with custom notes:

```powershell
gh release create v0.14.0 --title "v0.14.0" --notes-file path/to/notes.md
```

### 5. Watch the build

```powershell
gh run watch
```

---

## What Happens After a Tag Push

1. `.github/workflows/release.yml` triggers on `v*` tags.
2. Docker Buildx builds `linux/amd64` and `linux/arm64` images.
3. The `APP_VERSION` build arg bakes the tag name into the container's `VERSION` file.
4. Images are pushed to `ghcr.io/ytdl-stash/ytdl-stash` with tags:
   - `0.14.0` (full semver)
   - `0.14` (major.minor)
   - `0` (major)
   - `latest`

---

## Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| `git` | Version control | — |
| `gh` | GitHub CLI (releases) | `winget install GitHub.cli` |

---

## Authentication — THE RULE

**This repo uses exactly one credential: a classic PAT for the `spincity07` account, stored in the `gh` keyring. Both `gh` and `git push` read it. Never `jpittelkow`, never a second token, never a plaintext credential file.**

`jpittelkow` has `pull: true, push: false` here — it can never publish. If a push or release fails, that account is usually why.

### How it's wired

The PAT lives encrypted in the Windows keyring (`gh:github.com:spincity07`). `git` does not have its own copy — this repo's `.git/config` routes git's credential lookup through `gh`:

```ini
[credential "https://github.com"]
	helper =
	helper = !'C:\Program Files\GitHub CLI\gh.exe' auth git-credential
```

The **empty value first is load-bearing**: it resets the inherited helper chain (the global config would otherwise resolve to the wrong account). `git config --get-all` prints the raw list including globals — that is *not* the resolved chain, since entries before the last empty value are discarded. Don't read it and panic.

`gh auth git-credential` serves gh's **active account**, so `spincity07` must stay active:

```powershell
gh auth switch -h github.com -u spincity07
```

The remote URL pins the user (`https://spincity07@github.com/...`), so if the active account drifts, git **fails closed** with `could not read Password` rather than pushing as the wrong identity. That error almost always means the active account changed, not that the token died.

### Verify before releasing

```powershell
$env:GH_TOKEN = ''; $env:GITHUB_TOKEN = ''
gh auth token | ForEach-Object { $_.Substring(0,4) }      # want ghp_  (gho_ = wrong credential)
gh api -i user | Select-String '^X-Oauth-Scopes'          # want repo, workflow, write:packages
gh api repos/ytdl-stash/ytdl-stash --jq .permissions      # want push: true
```

### Rotating the PAT

1. Create a classic PAT at github.com/settings/tokens/new **signed in as spincity07**, scopes `repo`, `workflow`, `read:org`, `write:packages`.
2. Write it to a temporary file, then load it (PowerShell has **no `<` redirection** — pipe instead):

```powershell
Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
Get-Content "$env:USERPROFILE\.gh-token" | gh auth login -h github.com -p https --with-token
```

3. Delete the temporary file, then re-run the verify block above.

### Gotchas that have cost real time

- **`GH_TOKEN` overrides the keyring.** If set, `gh auth login --with-token` refuses outright. Keep it unset — it was removed from `HKCU:\Environment` on 2026-08-11.
- **`--with-token` fails silently on an empty file.** It stores nothing, the previous credential keeps working, and everything looks fine. Assert first: `(Get-Content $f -Raw).Trim().Length` should be ~40.
- **Check the token *type*, not just that auth works.** `gho_` is a browser-OAuth token, a different credential from your `ghp_` PAT — editing the PAT's scopes then has no effect, and gh's default OAuth scopes (`gist, read:org, repo`) look deceptively close to correct.
- **`git` and `gh` were once separate stores.** Answering the Credential Manager prompt during `git push` writes a `git:` entry that `gh` never reads. The config above eliminates this, but a reverted `.git/config` brings it back.

---

## Versioning Convention

We use **semver** (`MAJOR.MINOR.PATCH`):

- **Patch** — bug fixes, typo corrections, dependency bumps
- **Minor** — new features, new config options, UI changes (most releases)
- **Major** — breaking changes to config, database schema without migration, or API
