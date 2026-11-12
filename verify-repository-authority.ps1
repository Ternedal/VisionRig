param(
    [switch]$Json
)

# Read-only verification of the live GitHub settings that protect main.
#
# The verdict is bound to a checkout: it must be run from a clean checkout of
# main, and GitHub's main must stand on exactly that commit. With -Json the
# verdict is one JSON line for the Kaliv repository_authority release gate.
#
# Exit 0: PASS. Exit 2: a verdict that fails. Any other failure (no gh, not on
# main, a dirty checkout) throws.

$ErrorActionPreference = "Stop"
$Repo = "Ternedal/VisionRig"
$Branch = "main"
$Format = "visionrig-repository-authority"
$ExpectedChecks = @(
    @{ context = "test"; app_id = 15368 }
)

function Read-GitLine {
    param([Parameter(Mandatory = $true)][string[]]$Arguments, [Parameter(Mandatory = $true)][string]$Failure)
    $lines = @(& git -C $script:RepoRoot @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) { throw $Failure }
    return ,$lines
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required."
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required."
}

# --- the checkout this verdict is bound to ---------------------------------
$script:RepoRoot = (Resolve-Path $PSScriptRoot).Path
$branchLines = Read-GitLine -Arguments @("branch", "--show-current") -Failure "Could not read the current branch."
if ($branchLines.Count -ne 1 -or ([string]$branchLines[0]).Trim() -ne $Branch) {
    throw "Repository-authority verification must run from branch main."
}
$dirty = Read-GitLine -Arguments @("status", "--porcelain") -Failure "Could not read the checkout status."
if ($dirty.Count -gt 0) {
    throw "Repository-authority verification requires an exact clean main checkout."
}
$headLines = Read-GitLine -Arguments @("rev-parse", "HEAD") -Failure "Could not resolve HEAD."
$head = ([string]$headLines[0]).Trim().ToLowerInvariant()
if ($headLines.Count -ne 1 -or $head -notmatch '^[0-9a-f]{40}$') {
    throw "HEAD is not an exact Git revision."
}

# --- the live settings ------------------------------------------------------
$errors = New-Object System.Collections.Generic.List[string]

$raw = & gh api "repos/$Repo/branches/$Branch" 2>&1
if ($LASTEXITCODE -ne 0) {
    throw ("GitHub API read failed for the main branch. " + ($raw -join [Environment]::NewLine))
}
$branchInfo = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
$liveHead = ""
if ($null -ne $branchInfo.commit -and $null -ne $branchInfo.commit.sha) {
    $liveHead = ([string]$branchInfo.commit.sha).Trim().ToLowerInvariant()
}
if ($liveHead -ne $head) {
    $errors.Add("GitHub main head does not match the checkout revision")
}
$protected = ($branchInfo.protected -eq $true)
if (-not $protected) {
    $errors.Add("GitHub reports main as unprotected")
}

$protection = $null
$raw = & gh api "repos/$Repo/branches/$Branch/protection" --method GET 2>&1
if ($LASTEXITCODE -ne 0) {
    $errors.Add("main protection is unavailable")
} else {
    $protection = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
}

if ($null -ne $protection) {
    if ($null -eq $protection.required_status_checks) {
        $errors.Add("required_status_checks is missing")
    } elseif ($protection.required_status_checks.strict -ne $true) {
        $errors.Add("required_status_checks.strict is not true")
    }

    $actual = @()
    if ($null -ne $protection.required_status_checks -and $null -ne $protection.required_status_checks.checks) {
        $actual = @($protection.required_status_checks.checks | ForEach-Object {
            "$($_.context):$($_.app_id)"
        })
    }
    foreach ($check in $ExpectedChecks) {
        $key = "$($check.context):$($check.app_id)"
        if ($actual -notcontains $key) {
            $errors.Add("missing required check/source binding: $key")
        }
    }

    if ($null -eq $protection.required_pull_request_reviews) {
        $errors.Add("pull requests are not required")
    }
    if ($null -eq $protection.enforce_admins -or $protection.enforce_admins.enabled -ne $true) {
        $errors.Add("administrator enforcement is not enabled")
    }
    if ($null -eq $protection.required_conversation_resolution -or $protection.required_conversation_resolution.enabled -ne $true) {
        $errors.Add("conversation resolution is not required")
    }
    if ($null -ne $protection.allow_force_pushes -and $protection.allow_force_pushes.enabled -eq $true) {
        $errors.Add("force pushes are allowed")
    }
    if ($null -ne $protection.allow_deletions -and $protection.allow_deletions.enabled -eq $true) {
        $errors.Add("main deletion is allowed")
    }
}

$passed = ($errors.Count -eq 0)

if ($Json) {
    # Built as arrays and serialized with -InputObject: a pipeline would turn a
    # list of one into a bare value and an empty list into nothing.
    $requiredChecks = New-Object System.Collections.ArrayList
    foreach ($check in $ExpectedChecks) {
        [void]$requiredChecks.Add([ordered]@{ context = [string]$check.context; app_id = [int]$check.app_id })
    }
    $result = [ordered]@{
        format                 = $Format
        version                = 1
        repository             = $Repo
        branch                 = $Branch
        head_revision          = $head
        github_head_revision   = $liveHead
        protected              = $protected
        authority_mode         = $(if ($passed) { "classic" } else { $null })
        required_status_checks = $requiredChecks.ToArray()
        passed                 = $passed
        errors                 = $errors.ToArray()
        production_activation  = $false
    }
    ConvertTo-Json -InputObject $result -Depth 6 -Compress
} else {
    Write-Host "Repository authority: $(if ($passed) { 'PASS' } else { 'FAIL' })"
    Write-Host "Repository: $Repo"
    Write-Host "Branch: $Branch"
    Write-Host "Head: $head"
    if (-not $passed) {
        $errors | ForEach-Object { Write-Host " - $_" }
    } else {
        Write-Host "Required checks:"
        $ExpectedChecks | ForEach-Object { Write-Host " - $($_.context) (app_id=$($_.app_id))" }
    }
}

if (-not $passed) { exit 2 }
exit 0
