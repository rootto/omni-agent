# Helper script for an out-of-band Project IAM Admin to grant required deployment IAM permissions.
# Usage: .\set-iam-permissions.ps1 [-Project <project-id>] [-ProjectNumber <project-number>]
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

$AGENT_RUNTIME_SERVICE_ACCOUNT = "service-$ProjectNumber@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
$CLOUDBUILD_SERVICE_ACCOUNT = "$ProjectNumber@cloudbuild.gserviceaccount.com"

Write-Host "🛡️ Granting deployment IAM roles on project: $Project"
Write-Host "  • Runtime Service Account: $AGENT_RUNTIME_SERVICE_ACCOUNT"
Write-Host "  • Cloud Build Service Account: $CLOUDBUILD_SERVICE_ACCOUNT"
Write-Host ""

function Grant-Role {
    param([string]$Member, [string]$Role)
    Write-Host "  -> Granting $Role to $Member..."
    gcloud projects add-iam-policy-binding $Project --member="serviceAccount:$Member" --role=$Role --condition=None 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to grant $Role to $Member."
    }
}

Write-Host "1) Vertex AI Reasoning Engine Runtime SA ($AGENT_RUNTIME_SERVICE_ACCOUNT):"
Grant-Role -Member $AGENT_RUNTIME_SERVICE_ACCOUNT -Role "roles/aiplatform.user"
Grant-Role -Member $AGENT_RUNTIME_SERVICE_ACCOUNT -Role "roles/storage.objectAdmin"
Grant-Role -Member $AGENT_RUNTIME_SERVICE_ACCOUNT -Role "roles/iam.serviceAccountTokenCreator"

Write-Host ""
Write-Host "2) Cloud Build Service Account ($CLOUDBUILD_SERVICE_ACCOUNT):"
Grant-Role -Member $CLOUDBUILD_SERVICE_ACCOUNT -Role "roles/storage.objectAdmin"
Grant-Role -Member $CLOUDBUILD_SERVICE_ACCOUNT -Role "roles/logging.logWriter"

Write-Host ""
Write-Host "✅ All required deployment IAM permissions granted successfully!"
