# Deploy Omni-Agent to Gemini Enterprise infrastructure explicitly.
$ErrorActionPreference = "Stop"

# Source environment variables if .env exists
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

$PROJECT = $env:GOOGLE_CLOUD_PROJECT
$INSTANCE = $env:GEMINI_ENTERPRISE_INSTANCE
$PROJECT_NUMBER = if ($env:PROJECT_NUMBER) { $env:PROJECT_NUMBER } else { $env:GOOGLE_CLOUD_PROJECT_NUMBER }
$GEMINI_APP_LOCATION = if ($env:GEMINI_APP_LOCATION) { $env:GEMINI_APP_LOCATION } else { "global" }
$ARTIFACTS_BUCKET = if ($env:GCS_BUCKET_NAME) { $env:GCS_BUCKET_NAME } else { "geapp_agents_storage" } # Default, can be overridden via args

# Check if arguments provided
$remainingArgs = @()
$i = 0
while ($i -lt $args.Count) {
    if ($args[$i] -eq "--bucket" -and ($i + 1) -lt $args.Count) {
        $ARTIFACTS_BUCKET = $args[$i + 1]
        $i += 2
    } else {
        # Unrecognized param, let it pass to agents-cli
        for ($j = $i; $j -lt $args.Count; $j++) {
            $remainingArgs += $args[$j]
        }
        break
    }
}

Write-Host "🚀 Deploying Omni-Agent to project: $PROJECT"
Write-Host "🏢 Targeting Gemini Enterprise Instance: $INSTANCE"
Write-Host "🪣  Using Artifacts Bucket: $ARTIFACTS_BUCKET"

# Ensure bucket exists
$bucketExists = $false
try {
    gcloud storage ls "gs://$ARTIFACTS_BUCKET" --project "$PROJECT" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $bucketExists = $true
    }
} catch {
    $bucketExists = $false
}

if (-not $bucketExists) {
    Write-Host "Bucket gs://$ARTIFACTS_BUCKET does not exist or is inaccessible. Creating..."
    try {
        gcloud storage buckets create "gs://$ARTIFACTS_BUCKET" --project "$PROJECT" 2>&1 | Out-Null
    } catch {
        # Ignore creation error if bucket already exists or permission issue, equivalent to || true
    }
} else {
    Write-Host "Bucket gs://$ARTIFACTS_BUCKET already exists."
}

# We must update .env so agents-cli injects it to the deployed reasoning engine!
if (Test-Path ".env") {
    $envContent = Get-Content ".env"
    $envContent = $envContent -replace "^LOGS_BUCKET_NAME=.*", "LOGS_BUCKET_NAME=$ARTIFACTS_BUCKET"
    $envContent = $envContent -replace "^GCS_BUCKET_NAME=.*", "GCS_BUCKET_NAME=$ARTIFACTS_BUCKET"
    $envContent | Set-Content ".env"
}
$env:LOGS_BUCKET_NAME = $ARTIFACTS_BUCKET
$env:GCS_BUCKET_NAME = $ARTIFACTS_BUCKET

Write-Host "🛡️ Verifying required Cloud APIs..."
gcloud services enable cloudresourcemanager.googleapis.com aiplatform.googleapis.com cloudbuild.googleapis.com storage.googleapis.com discoveryengine.googleapis.com iam.googleapis.com --project "$PROJECT"
if ($LASTEXITCODE -ne 0) {
    throw "gcloud services enable failed with exit code $LASTEXITCODE"
}

$GOOGLE_CLOUD_PROJECT_NUMBER = $env:GOOGLE_CLOUD_PROJECT_NUMBER
if ([string]::IsNullOrEmpty($GOOGLE_CLOUD_PROJECT_NUMBER)) {
    $GOOGLE_CLOUD_PROJECT_NUMBER = Read-Host "Enter your Google Cloud Project Number (numeric ID)"
    $env:GOOGLE_CLOUD_PROJECT_NUMBER = $GOOGLE_CLOUD_PROJECT_NUMBER
    if (Test-Path ".env") {
        $envContent = Get-Content ".env"
        if ($envContent -match "^GOOGLE_CLOUD_PROJECT_NUMBER=") {
            $envContent = $envContent -replace "^GOOGLE_CLOUD_PROJECT_NUMBER=.*", "GOOGLE_CLOUD_PROJECT_NUMBER=$GOOGLE_CLOUD_PROJECT_NUMBER"
            $envContent | Set-Content ".env"
        } else {
            Add-Content -Path ".env" -Value "GOOGLE_CLOUD_PROJECT_NUMBER=$GOOGLE_CLOUD_PROJECT_NUMBER"
        }
    }
}

$AGENT_RUNTIME_SERVICE_ACCOUNT = if ($env:AGENT_RUNTIME_SERVICE_ACCOUNT) {
    $env:AGENT_RUNTIME_SERVICE_ACCOUNT
} else {
    "service-$($GOOGLE_CLOUD_PROJECT_NUMBER)@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
}
$CLOUDBUILD_SERVICE_ACCOUNT = "$($GOOGLE_CLOUD_PROJECT_NUMBER)@cloudbuild.gserviceaccount.com"
$env:AGENT_RUNTIME_SERVICE_ACCOUNT = $AGENT_RUNTIME_SERVICE_ACCOUNT

Write-Host "🛡️ Granting deployment IAM roles to Reasoning Engine and Cloud Build service accounts..."
function Grant-RoleOrWarn {
    param([string]$Member, [string]$Role)
    try {
        gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$Member" --role="$Role" --condition=None 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "⚠️  Could not grant $Role to $Member (insufficient Project IAM Admin permission)."
            Write-Host "    If deployment fails, ask an IAM Admin to run: .\set-iam-permissions.ps1"
        }
    } catch {
        Write-Host "⚠️  Could not grant $Role to $Member (insufficient Project IAM Admin permission)."
        Write-Host "    If deployment fails, ask an IAM Admin to run: .\set-iam-permissions.ps1"
    }
}

Grant-RoleOrWarn -Member $AGENT_RUNTIME_SERVICE_ACCOUNT -Role "roles/aiplatform.user"
Grant-RoleOrWarn -Member $AGENT_RUNTIME_SERVICE_ACCOUNT -Role "roles/storage.objectAdmin"
Grant-RoleOrWarn -Member $AGENT_RUNTIME_SERVICE_ACCOUNT -Role "roles/iam.serviceAccountTokenCreator"
Grant-RoleOrWarn -Member $CLOUDBUILD_SERVICE_ACCOUNT -Role "roles/storage.objectAdmin"
Grant-RoleOrWarn -Member $CLOUDBUILD_SERVICE_ACCOUNT -Role "roles/logging.logWriter"

# Pass the project explicitly to the agents-cli deployments.
agents-cli deploy --project "$PROJECT" --no-confirm-project @remainingArgs
if ($LASTEXITCODE -ne 0) {
    throw "agents-cli deploy failed with exit code $LASTEXITCODE"
}

# Publish to the targeted Gemini Enterprise App Instance to make the agent visible in the environment.
$APP_ID = "projects/$PROJECT_NUMBER/locations/$GEMINI_APP_LOCATION/collections/default_collection/engines/$INSTANCE"
Write-Host "🔗 Publishing Agent to Gemini Enterprise..."
agents-cli publish gemini-enterprise --project "$PROJECT" --gemini-enterprise-app-id "$APP_ID"
if ($LASTEXITCODE -ne 0) {
    throw "agents-cli publish gemini-enterprise failed with exit code $LASTEXITCODE"
}
