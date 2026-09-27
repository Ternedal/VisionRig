param()

$ErrorActionPreference = "Stop"
$Repo = "Ternedal/VisionRig"
$Branch = "main"
$ExpectedChecks = @(
    @{ context = "test"; app_id = 15368 }
)

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required."
}

$raw = & gh api "repos/$Repo/branches/$Branch/protection" --method GET 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error ("Repository authority FAIL: main protection is unavailable. " + ($raw -join [Environment]::NewLine))
    exit 1
}
$protection = ($raw -join [Environment]::NewLine) | ConvertFrom-Json

$errors = New-Object System.Collections.Generic.List[string]

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

if ($errors.Count -gt 0) {
    Write-Host "Repository authority: FAIL"
    $errors | ForEach-Object { Write-Host " - $_" }
    exit 1
}

Write-Host "Repository authority: PASS"
Write-Host "Repository: $Repo"
Write-Host "Branch: $Branch"
Write-Host "Required checks:"
$ExpectedChecks | ForEach-Object { Write-Host " - $($_.context) (app_id=$($_.app_id))" }
