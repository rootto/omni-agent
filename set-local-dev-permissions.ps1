# Helper script to grant local development IAM permissions for local testing and playground.
# Usage: .\set-local-dev-permissions.ps1 [-Project <project-id>] [-ProjectNumber <project-number>]
param(
    [string]$Project = "",
    [string]$ProjectNumber = ""
)

$ErrorActionPreference = "Stop"

if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $idx = $line.IndexOf("=")
            if ($idx -gt 0) {
                $key = $line.Substring(0, $idx).Trim()
                $value = $line.Substring($idx + 1).Trim()
                if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
                [Environment]::SetEnvironmentVariable($key, $value, "Process")
            }
        }
    }
}

if (-not $Project) {
    $Project = $env:GOOGLE_CLOUD_PROJECT
}
if (-not $Project) {
    $Project = Read-Host "Enter Google Cloud Project ID"
}

if (-not $ProjectNumber) {
    $ProjectNumber = if ($env:PROJECT_NUMBER) { $env:PROJECT_NUMBER } else { $env:GOOGLE_CLOUD_PROJECT_NUMBER }
}
if (-not $ProjectNumber) {
    $ProjectNumber = Read-Host "Enter Google Cloud Project Number (numeric ID)"
}

$COMPUTE_SERVICE_ACCOUNT = if ($env:COMPUTE_SERVICE_ACCOUNT) {
    $env:COMPUTE_SERVICE_ACCOUNT
} else {
    "$ProjectNumber-compute@developer.gserviceaccount.com"
}

Write-Host "🛡️ Granting local development IAM roles on project: $Project"
Write-Host "  • Compute Service Account: $COMPUTE_SERVICE_ACCOUNT"
Write-Host ""

function Grant-Role {
    param([string]$Member, [string]$Role)
    Write-Host "  -> Granting $Role to $Member..."
    gcloud projects add-iam-policy-binding $Project --member="serviceAccount:$Member" --role=$Role --condition=None 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to grant $Role to $Member."
    }
}

Grant-Role -Member $COMPUTE_SERVICE_ACCOUNT -Role "roles/aiplatform.user"
Grant-Role -Member $COMPUTE_SERVICE_ACCOUNT -Role "roles/storage.objectAdmin"
Grant-Role -Member $COMPUTE_SERVICE_ACCOUNT -Role "roles/iam.serviceAccountTokenCreator"

Write-Host ""
Write-Host "✅ All required local development IAM permissions granted successfully!"
