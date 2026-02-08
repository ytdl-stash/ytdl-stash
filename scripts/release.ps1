<#
.SYNOPSIS
    Create a new ytdl-stash release: commit, tag, push, and publish a GitHub release.

.DESCRIPTION
    Automates the full release workflow:
      1. Verify prerequisites (git, gh CLI, clean or dirty tree)
      2. Calculate the next version from the latest tag
      3. Stage and commit (if there are uncommitted changes)
      4. Create a git tag
      5. Push the branch and tag to origin
      6. Create a GitHub release with auto-generated notes

    The release.yml workflow fires on the tag push and builds the Docker image.

.PARAMETER Bump
    Version bump type: major, minor, or patch.  Default: minor.

.PARAMETER Version
    Explicit version string (e.g. "0.14.0"). Overrides -Bump when provided.
    Do NOT include the "v" prefix -- the script adds it.

.PARAMETER Message
    Optional one-line commit message.  When omitted you will be prompted.

.PARAMETER DryRun
    Show what would happen without making any changes.

.PARAMETER NoCommit
    Skip the commit step (use when the working tree is already clean and
    you just want to tag + release the current HEAD).

.PARAMETER Yes
    Skip interactive prompts: use "Release vX.Y.Z" as commit message and
    proceed without confirmation. Use for automation/CI.

.EXAMPLE
    .\scripts\release.ps1                          # minor bump, prompted for commit msg
    .\scripts\release.ps1 -Bump patch              # patch bump
    .\scripts\release.ps1 -Version 1.0.0           # explicit version
    .\scripts\release.ps1 -DryRun                  # preview only
    .\scripts\release.ps1 -NoCommit                # tag + release current HEAD
#>

[CmdletBinding()]
param(
    [ValidateSet("major", "minor", "patch")]
    [string]$Bump = "minor",

    [string]$Version,
    [string]$Message,
    [switch]$DryRun,
    [switch]$NoCommit,
    [switch]$Yes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# -- Helpers ------------------------------------------------------------

function Write-Step  { param([string]$Text) Write-Host "`n> $Text" -ForegroundColor Cyan }
function Write-Info  { param([string]$Text) Write-Host "  $Text" -ForegroundColor Gray }
function Write-Ok    { param([string]$Text) Write-Host "  OK: $Text" -ForegroundColor Green }
function Write-Warn  { param([string]$Text) Write-Host "  WARN: $Text" -ForegroundColor Yellow }
function Write-Err   { param([string]$Text) Write-Host "  ERR: $Text" -ForegroundColor Red }

function Invoke-Cmd {
    <# Run a command, optionally as dry-run. Returns stdout.
       Uses cmd /c to avoid PowerShell treating git stderr as errors. #>
    param(
        [string]$Label,
        [string]$Cmd,
        [switch]$AllowFailure
    )
    if ($DryRun) {
        Write-Info "[dry-run] $Cmd"
        return ""
    }
    Write-Info $Label
    # Run via cmd /c so that stderr from native commands (e.g. git progress
    # messages) does not trigger PowerShell's NativeCommandError handling.
    $output = cmd /c "$Cmd 2>&1"
    $exitCode = $LASTEXITCODE
    if ($exitCode -and -not $AllowFailure) {
        Write-Err "Command failed (exit $exitCode): $Cmd"
        Write-Err ($output | Out-String)
        exit 1
    }
    return ($output | Out-String).Trim()
}

function Get-LatestTag {
    $tag = (git tag --sort=-creatordate | Select-Object -First 1) 2>$null
    if (-not $tag) { return $null }
    return $tag.Trim()
}

function Parse-SemVer {
    param([string]$Tag)
    if ($Tag -match '^v?(\d+)\.(\d+)\.(\d+)') {
        return @{
            Major = [int]$Matches[1]
            Minor = [int]$Matches[2]
            Patch = [int]$Matches[3]
        }
    }
    return $null
}

function Get-BumpedVersion {
    param([hashtable]$Ver, [string]$BumpType)
    switch ($BumpType) {
        "major" { return "$($Ver.Major + 1).0.0" }
        "minor" { return "$($Ver.Major).$($Ver.Minor + 1).0" }
        "patch" { return "$($Ver.Major).$($Ver.Minor).$($Ver.Patch + 1)" }
    }
}

# -- Prereqs ------------------------------------------------------------

Write-Host "`n=== ytdl-stash release ===" -ForegroundColor Magenta

Write-Step "Checking prerequisites"

# Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Err "git is not installed or not in PATH"; exit 1
}
Write-Ok "git found"

# GitHub CLI
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Err "gh (GitHub CLI) is not installed or not in PATH"; exit 1
}
Write-Ok "gh found"

# Must be in a git repo
$repoRoot = (git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE) {
    Write-Err "Not inside a git repository"; exit 1
}
Write-Ok "In repo: $repoRoot"

# Must be on main
$branch = (git branch --show-current).Trim()
if ($branch -ne "main") {
    Write-Warn "Current branch is '$branch', not 'main'"
    $continue = Read-Host "  Continue anyway? [y/N]"
    if ($continue -notin @("y", "Y", "yes")) { exit 0 }
}
Write-Ok "Branch: $branch"

# -- Version ------------------------------------------------------------

Write-Step "Determining version"

$latestTag = Get-LatestTag
if ($latestTag) {
    Write-Info "Latest tag: $latestTag"
} else {
    Write-Info "No existing tags found -- starting from 0.0.0"
    $latestTag = "v0.0.0"
}

if ($Version) {
    # Explicit version provided
    $nextVersion = $Version -replace '^v', ''
} else {
    $parsed = Parse-SemVer $latestTag
    if (-not $parsed) {
        Write-Err "Cannot parse latest tag '$latestTag' as semver"; exit 1
    }
    $nextVersion = Get-BumpedVersion $parsed $Bump
}

$nextTag = "v$nextVersion"

# Check tag doesn't already exist
$existingTags = git tag -l $nextTag
if ($existingTags) {
    Write-Err "Tag $nextTag already exists. Pick a different version."; exit 1
}

if ($Version) {
    Write-Ok "Next release: $nextTag -- explicit version"
} else {
    Write-Ok "Next release: $nextTag -- $Bump bump from $latestTag"
}

# -- Working tree -------------------------------------------------------

Write-Step "Checking working tree"

$statusOutput = (git status --porcelain)
$isDirty = [bool]$statusOutput

if ($isDirty -and $NoCommit) {
    Write-Err "Working tree has uncommitted changes but -NoCommit was specified."
    Write-Err "Commit manually first or drop -NoCommit."
    exit 1
}

if ($isDirty) {
    $changedFiles = @($statusOutput -split "`n").Count
    Write-Info "$changedFiles file(s) with uncommitted changes"

    # Show summary
    git diff --stat
    Write-Host ""
    $untracked = @(git ls-files --others --exclude-standard)
    if ($untracked.Count -gt 0) {
        Write-Info "$($untracked.Count) untracked file(s):"
        foreach ($f in $untracked) { Write-Info "  + $f" }
    }
} else {
    Write-Ok "Working tree is clean"
}

# -- Commit -------------------------------------------------------------

if ($isDirty) {
    Write-Step "Committing changes"

    if ($DryRun) {
        Write-Info "[dry-run] Would stage and commit all changes"
    } else {
        if (-not $Message) {
            if ($Yes) {
                $Message = "Release $nextTag"
            } else {
                $Message = Read-Host "  Commit message (blank to abort)"
                if (-not $Message) {
                    Write-Warn "No commit message -- aborting."; exit 0
                }
            }
        }

        Invoke-Cmd -Label "Staging all changes" -Cmd "git add -A"

        # Write message to temp file to avoid PowerShell quoting issues
        # Use UTF8NoBOM to prevent a BOM from appearing in the commit message
        $msgFile = [System.IO.Path]::GetTempFileName()
        [System.IO.File]::WriteAllText($msgFile, $Message, [System.Text.UTF8Encoding]::new($false))
        Invoke-Cmd -Label "Creating commit" -Cmd "git commit -F `"$msgFile`""
        Remove-Item $msgFile -ErrorAction SilentlyContinue

        Write-Ok "Committed"
    }
} elseif (-not $NoCommit) {
    Write-Info "Nothing to commit -- proceeding to tag"
}

# -- Generate release notes ---------------------------------------------

Write-Step "Generating release notes"

$commitLog = ""
if (-not $DryRun) {
    $commitLog = (git log "$latestTag..HEAD" --pretty=format:"- %s (%h)" 2>$null)
    if (-not $commitLog) {
        $commitLog = (git log --oneline -5 --pretty=format:"- %s (%h)")
    }
}
Write-Info "Commits since $($latestTag):"
$commitLog -split "`n" | ForEach-Object { Write-Info "  $_" }

$releaseNotes = "## What's Changed`n`n$commitLog`n`n**Full changelog**: https://github.com/jpittelkow/ytdl-stash/compare/$latestTag...$nextTag"

# -- Confirmation -------------------------------------------------------

Write-Host ""
Write-Host "=== Release Plan ===" -ForegroundColor Magenta
Write-Host "  Version : $nextTag" -ForegroundColor White
Write-Host "  Branch  : $branch" -ForegroundColor White
$commitCount = @($commitLog -split "`n" | Where-Object { $_ }).Count
Write-Host "  Commits : $commitCount since $latestTag" -ForegroundColor White
if ($DryRun) {
    Write-Host "  Mode    : DRY RUN (no changes)" -ForegroundColor Yellow
}
Write-Host ""

if (-not $DryRun) {
    if (-not $Yes) {
        $confirm = Read-Host "Proceed? [y/N]"
        if ($confirm -notin @("y", "Y", "yes")) {
            Write-Warn "Aborted."; exit 0
        }
    }
}

# -- Tag ----------------------------------------------------------------

Write-Step "Creating tag $nextTag"
Invoke-Cmd -Label "git tag $nextTag" -Cmd "git tag $nextTag"
Write-Ok "Tag created"

# -- Push ---------------------------------------------------------------

Write-Step "Pushing to origin"
Invoke-Cmd -Label "Pushing branch" -Cmd "git push origin $branch"
Invoke-Cmd -Label "Pushing tag"    -Cmd "git push origin $nextTag"
Write-Ok "Pushed branch and tag"

# -- GitHub Release -----------------------------------------------------

Write-Step "Creating GitHub release"

$notesFile = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($notesFile, $releaseNotes, [System.Text.UTF8Encoding]::new($false))

Invoke-Cmd -Label "gh release create $nextTag" `
           -Cmd "gh release create $nextTag --title `"$nextTag`" --notes-file `"$notesFile`""

Remove-Item $notesFile -ErrorAction SilentlyContinue
Write-Ok "GitHub release created"

# -- Done ---------------------------------------------------------------

Write-Host ""
Write-Host "=== Release $nextTag complete! ===" -ForegroundColor Green
Write-Host ""
Write-Host "  Release  : https://github.com/jpittelkow/ytdl-stash/releases/tag/$nextTag" -ForegroundColor White
Write-Host "  Workflow : https://github.com/jpittelkow/ytdl-stash/actions" -ForegroundColor White
Write-Host ""
Write-Host "  The release workflow will build and push the Docker image to ghcr.io." -ForegroundColor Gray
Write-Host "  Run 'gh run watch' to follow the build." -ForegroundColor Gray
Write-Host ""
