param(
    [switch]$StatusOnly,
    [switch]$AllowDetachedHeadForCi,
    [switch]$QualifyAndroid,
    [switch]$QualifyQuest,
    [switch]$QualifyKinect,
    [string]$AndroidSourceId = "kaliv-android",
    [string]$QuestSourceId = "kaliv-quest",
    [string]$KinectModelManifest,
    [string]$ModelRigWorkerUrl,
    [int]$KinectFrames = 5,
    [long]$CandidateCiRunId = 0,
    [string]$ExpectedVersion = "0.99.0",
    [string]$TargetVersion = "1.0.0",
    [string]$VisionRigUrl = "http://127.0.0.1:8111",
    [string]$ValidationDir = "validation"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
Push-Location $RepoRoot
try {
    function Invoke-Checked {
        param(
            [Parameter(Mandatory = $true)][string]$Command,
            [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
        )
        $resolved = Get-Command $Command -ErrorAction SilentlyContinue
        if (-not $resolved) { throw "Required command not found: $Command" }
        & $Command @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Command failed with exit code $LASTEXITCODE"
        }
    }

    function Assert-CleanCheckout {
        $dirty = (& git status --porcelain=v1)
        if ($LASTEXITCODE -ne 0) { throw "git status failed" }
        if ($dirty) {
            throw "Release gate requires a clean checkout. Commit or discard local changes first."
        }
        $branchRaw = (& git branch --show-current)
        if ($LASTEXITCODE -ne 0) { throw "Unable to resolve current branch" }
        $branch = if ($null -eq $branchRaw) { "" } else { ([string]$branchRaw).Trim() }
        $detachedCiStatusCheck = (
            $branch -eq "" -and
            $StatusOnly -and
            $AllowDetachedHeadForCi -and
            $env:GITHUB_ACTIONS -eq "true"
        )
        if (-not $detachedCiStatusCheck -and $branch -ne "main") {
            $displayBranch = if ($branch) { $branch } else { "<detached>" }
            throw "Release gate must run from main; current branch is '$displayBranch'"
        }
        $sha = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0 -or $sha -notmatch "^[0-9a-f]{40}$") {
            throw "Unable to resolve exact git SHA"
        }
        $versionLine = Select-String -Path "pyproject.toml" -Pattern '^version = "([^"]+)"$' | Select-Object -First 1
        if (-not $versionLine) {
            throw "Unable to read VisionRig package version from pyproject.toml"
        }
        $version = $versionLine.Matches[0].Groups[1].Value
        if ($version -ne $ExpectedVersion) {
            throw "Expected candidate version $ExpectedVersion but checkout reports $version"
        }
        return $sha
    }

    $sha = Assert-CleanCheckout
    $env:VISIONRIG_GIT_SHA = $sha
    $validationPath = Join-Path $RepoRoot $ValidationDir
    New-Item -ItemType Directory -Force -Path $validationPath | Out-Null

    Write-Host "============================================================"
    Write-Host "VISIONRIG V1 RELEASE GATE"
    Write-Host "Revision: $sha"
    Write-Host "Candidate: $ExpectedVersion"
    Write-Host "Target: $TargetVersion"
    Write-Host "Validation: $validationPath"
    Write-Host "Production authority: FALSE"
    Write-Host "============================================================"

    Invoke-Checked "visionrig-release-status" "--json"

    $collectPhysical = $QualifyAndroid -or $QualifyQuest -or $QualifyKinect
    if ($StatusOnly -or (-not $collectPhysical -and $CandidateCiRunId -le 0)) {
        Write-Host "Status-only run complete. No release evidence was manufactured."
        exit 0
    }

    $evidence = New-Object System.Collections.Generic.List[string]

    if ($QualifyAndroid) {
        $androidReport = Join-Path $validationPath "visionrig-android-physical.json"
        $argsAndroid = @("--visionrig-url", $VisionRigUrl, "--expected-sha", $sha, "--source-id", $AndroidSourceId, "--report", $androidReport)
        Invoke-Checked "visionrig-qualify-physical" @argsAndroid
        $evidence.Add($androidReport)
    }

    if ($QualifyQuest) {
        $questReport = Join-Path $validationPath "visionrig-quest-physical.json"
        $argsQuest = @("--visionrig-url", $VisionRigUrl, "--expected-sha", $sha, "--source-id", $QuestSourceId, "--report", $questReport)
        Invoke-Checked "visionrig-qualify-physical" @argsQuest
        $evidence.Add($questReport)
    }

    if ($QualifyKinect) {
        if (-not $KinectModelManifest) { throw "-QualifyKinect requires -KinectModelManifest" }
        if (-not $ModelRigWorkerUrl) { throw "-QualifyKinect requires -ModelRigWorkerUrl" }
        $kinectReport = Join-Path $validationPath "visionrig-kinect-physical-acceptance.json"
        $argsKinect = @("--expected-sha", $sha, "--model-manifest", $KinectModelManifest, "--modelrig-worker-url", $ModelRigWorkerUrl, "--frames", ([string]$KinectFrames), "--output", $kinectReport)
        Invoke-Checked "visionrig-kinect-acceptance" @argsKinect
        $evidence.Add($kinectReport)
    }

    $bundle = Join-Path $validationPath "visionrig-release-evidence-bundle.json"
    if ($evidence.Count -gt 0) {
        $bundleArgs = @("--expected-sha", $sha, "--expected-version", $ExpectedVersion, "--output", $bundle) + $evidence.ToArray()
        Invoke-Checked "visionrig-release-evidence" @bundleArgs
    } elseif (-not (Test-Path $bundle)) {
        throw "No physical evidence was collected and no existing release bundle exists at $bundle"
    }

    if ($CandidateCiRunId -gt 0) {
        $promotion = Join-Path $validationPath "visionrig-v1-release-promotion.json"
        $promotionArgs = @("--expected-sha", $sha, "--candidate-version", $ExpectedVersion, "--target-version", $TargetVersion, "--ci-run-id", ([string]$CandidateCiRunId), "--output", $promotion, $bundle)
        Invoke-Checked "visionrig-release-promote" @promotionArgs
    } else {
        Write-Host "Physical release bundle is ready. Promotion skipped because -CandidateCiRunId was not supplied."
    }

    Invoke-Checked "visionrig-release-status" "--json"
    Write-Host "============================================================"
    Write-Host "Release gate completed for candidate $sha"
    Write-Host "No version bump or production activation was performed."
    Write-Host "============================================================"
}
finally {
    Pop-Location
}
