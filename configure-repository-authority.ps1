param(
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$Repo = "Ternedal/VisionRig"
$Branch = "main"

$Checks = @(
    @{ context = "test"; app_id = 15368 }
)

$Body = [ordered]@{
    required_status_checks = [ordered]@{
        strict = $true
        checks = $Checks
    }
    enforce_admins = $true
    required_pull_request_reviews = [ordered]@{
        dismiss_stale_reviews = $false
        require_code_owner_reviews = $false
        required_approving_review_count = 0
    }
    restrictions = $null
    required_conversation_resolution = $true
    allow_force_pushes = $false
    allow_deletions = $false
} | ConvertTo-Json -Depth 8

if (-not $Apply) {
    Write-Host "DRY RUN - no GitHub setting was changed."
    Write-Host "Repository: $Repo"
    Write-Host "Branch: $Branch"
    Write-Output $Body
    Write-Host ""
    Write-Host "Re-run with -Apply using an identity with repository Administration: write."
    exit 0
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required."
}

$Body | & gh api "repos/$Repo/branches/$Branch/protection" --method PUT --input - | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub rejected the branch-protection update."
}

& (Join-Path $PSScriptRoot "verify-repository-authority.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "GitHub accepted a write, but live repository-authority verification did not PASS."
}
