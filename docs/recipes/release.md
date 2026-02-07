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
4. Images are pushed to `ghcr.io/jpittelkow/ytdl-stash` with tags:
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

## Versioning Convention

We use **semver** (`MAJOR.MINOR.PATCH`):

- **Patch** — bug fixes, typo corrections, dependency bumps
- **Minor** — new features, new config options, UI changes (most releases)
- **Major** — breaking changes to config, database schema without migration, or API
