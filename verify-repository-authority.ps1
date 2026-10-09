param(
    [switch]$Json,
    [string]$ExpectedSha = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Repo = "Ternedal/VisionRig"
$Branch = "main"
$ExpectedChecks = @(
    @{ context = "test"; app_id = 15368 }
)
$Errors = New-Object System.Collections.Generic.List[string]
$Head = $null
$RemoteMainSha = $null
$Protected = $false

function Read-ApiJson {
    param([Parameter(Mandatory = $true)][string]$Path)
    $raw = @(& gh api $Path 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub API could not be read: $Path"
    }
    try { return (($raw -join "`n") | ConvertFrom-Json -Depth 60) }
    catch { throw "GitHub API did not return valid JSON: $Path" }
}

# Only an exact, clean local main checkout may be used to examine live settings.
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$branchRaw = @(& git -C $root branch --show-current 2>&1)
if ($LASTEXITCODE -ne 0 -or $branchRaw.Count -ne 1 -or ([string]$branchRaw[0]).Trim() -ne $Branch) {
    $Errors.Add("local checkout is not on main")
}
$statusRaw = @(& git -C $root status --porcelain --untracked-files=all 2>&1)
if ($LASTEXITCODE -ne 0 -or $statusRaw.Count -gt 0) {
    $Errors.Add("local checkout is not exactly clean")
}
$headRaw = @(& git -C $root rev-parse HEAD 2>&1)
if ($LASTEXITCODE -ne 0 -or $headRaw.Count -ne 1 -or ([string]$headRaw[0]).Trim() -notmatch '^[0-9a-f]{40}$') {
    $Errors.Add("local HEAD is not a readable exact Git SHA")
} else {
    $Head = ([string]$headRaw[0]).Trim().ToLowerInvariant()
}
if ($ExpectedSha -and $ExpectedSha -cnotmatch '^[0-9a-f]{40}$') {
    $Errors.Add("ExpectedSha must be 40 lowercase hex characters")
}
if ($ExpectedSha -and $Head -and $ExpectedSha -cne $Head) {
    $Errors.Add("local HEAD differs from explicitly pinned ExpectedSha")
}

if ($Errors.Count -eq 0) {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        $Errors.Add("GitHub CLI (gh) is required")
    } else {
        try {
            $branchInfo = Read-ApiJson -Path "repos/$Repo/branches/$Branch"
            $RemoteMainSha = [string]$branchInfo.commit.sha
            $Protected = $branchInfo.protected -eq $true
            if ($RemoteMainSha -cne $Head) {
                $Errors.Add("live remote main SHA differs from exact local HEAD")
            }
            if (-not $Protected) { $Errors.Add("live main is not protected") }
            # Lack of permissions to read this endpoint is never a PASS.
            $protection = Read-ApiJson -Path "repos/$Repo/branches/$Branch/protection"
            if ($null -eq $protection.required_status_checks) {
                $Errors.Add("required status checks missing")
            } else {
                if ($protection.required_status_checks.strict -ne $true) {
                    $Errors.Add("strict required status checks disabled")
                }
                $actual = @($protection.required_status_checks.checks | ForEach-Object {
                    "$($_.context):$($_.app_id)"
                })
                foreach ($check in $ExpectedChecks) {
                    $key = "$($check.context):$($check.app_id)"
                    if ($actual -notcontains $key) {
                        $Errors.Add("missing required check/source binding: $key")
                    }
                }
            }
            if ($null -eq $protection.required_pull_request_reviews) {
                $Errors.Add("pull requests are not required")
            }
            if ($null -eq $protection.enforce_admins -or $protection.enforce_admins.enabled -ne $true) {
                $Errors.Add("administrator enforcement is not enabled")
            }
            if ($null -eq $protection.required_conversation_resolution -or
                $protection.required_conversation_resolution.enabled -ne $true) {
                $Errors.Add("conversation resolution is not required")
            }
            # Missing/unknown controls cannot be read as safely disabled.
            if ($null -eq $protection.allow_force_pushes -or $protection.allow_force_pushes.enabled -ne $false) {
                $Errors.Add("force pushes are allowed or status is unknown")
            }
            if ($null -eq $protection.allow_deletions -or $protection.allow_deletions.enabled -ne $false) {
                $Errors.Add("main deletion is allowed or status is unknown")
            }
        } catch {
            # Do not leak gh stderr/credentials or reinterpret 403 as green.
            $Errors.Add("live GitHub protection data inaccessible or invalid")
        }
    }
}

$PolicyObserved = $Errors.Count -eq 0
$result = [ordered]@{
    schema = "visionrig-repository-authority-observation/v1"
    repository = $Repo
    branch = $Branch
    git_sha = $Head
    remote_main_sha = $RemoteMainSha
    protected = [bool]$Protected
    policy_observation_passed = [bool]$PolicyObserved
    errors = @($Errors.ToArray())
    # This legacy policy check does not independently authenticate complete
    # bypass/ruleset/administration semantics or issue a V1 evidence receipt.
    live_repository_authority_passed = $false
    release_gate_satisfied = $false
    release_ready = $false
    production_activation = $false
}
if ($Json) {
    $result | ConvertTo-Json -Depth 20 -Compress
} else {
    Write-Host "$Repo repository settings observation: $(if ($PolicyObserved) { 'PASS' } else { 'FAIL' })"
    Write-Host "Local SHA: $Head"
    Write-Host "Remote main SHA: $RemoteMainSha"
    foreach ($item in $Errors) { Write-Host " - $item" }
    Write-Host "This is NOT cross-repository V1 repository-authority or release evidence."
}
if (-not $PolicyObserved) { exit 2 }
exit 0
