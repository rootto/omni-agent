# Omni-Agent

Omni-Agent is an AI-powered conversational video generation and editing assistant built on top of **Google's Gemini Omni** (`gemini-omni-flash-preview`), executing high-speed cinematic video creation and editing through Vertex AI Reasoning Engine and Gemini Enterprise.

---

## Quick Start

### 1. Install Prerequisites (`uv` & `uvx`)

This project uses **uv** for fast Python package and tool management. Installing `uv` automatically installs the `uvx` tool runner:

**macOS / Linux (`bash`):**
```bash
# Install uv and uvx
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or install via Homebrew
brew install uv

# Install Google Agents CLI via uvx
uvx google-agents-cli setup
```

**Windows (`PowerShell`):**
```powershell
# Install uv and uvx
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or install via winget
winget install --id=astral-sh.uv -e

# Install Google Agents CLI via uvx
uvx google-agents-cli setup
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and configure your target environment variables:

**macOS / Linux (`bash`):**
```bash
cp .env.example .env
```

**Windows (`PowerShell`):**
```powershell
Copy-Item .env.example .env
```

Required variables in `.env`:
- `GOOGLE_CLOUD_PROJECT`: Your Google Cloud Project ID (e.g. `omini-test-agent`).
- `GOOGLE_CLOUD_PROJECT_NUMBER` / `PROJECT_NUMBER`: Your numeric Google Cloud Project Number (e.g. `687484203981`).
- `GEMINI_ENTERPRISE_INSTANCE`: Your target Gemini Enterprise engine instance name.
- `GEMINI_APP_LOCATION`: Your target Gemini Enterprise engine location (`global`, `eu`, or `us`).
- `GCS_BUCKET_NAME` & `LOGS_BUCKET_NAME`: Google Cloud Storage bucket name for video artifacts and logs.
- `GOOGLE_GENAI_USE_ENTERPRISE`: Set to `1` to use Vertex AI / Enterprise endpoints.
- `AGENT_MODEL_ID`: Orchestration model (default: `gemini-3.5-flash`).
- `OMNI_MODEL_ID`: Video generation model (default: `gemini-omni-flash-preview`).

### 3. Authenticate & Install Project Dependencies

Before running the deployment script in a fresh environment, you must log in with Google Cloud Application Default Credentials (ADC) and install project dependencies locally.

1. **Authenticate with Google Cloud ADC:**
   ```bash
   gcloud auth application-default login
   ```

2. **Install Project Dependencies:**
   ```bash
   uv sync
   # or
   agents-cli install
   ```

> [!WARNING]
> **Troubleshooting Deployment Error Code 3 (`Build failed...`):**
> If you get `Error: Deployment failed: {'code': 3, 'message': 'Build failed. The issue might be caused by incorrect code, requirements.txt file or other dependencies...'}` when deploying in a fresh environment, verify the following:
> 1. **Required Cloud APIs**: Vertex AI Reasoning Engine builds your container in Google Cloud Build (`cloudbuild.googleapis.com`). Our deployment scripts (`deploy-agent.sh` / `deploy-agent.ps1`) automatically enable `cloudbuild.googleapis.com`, `aiplatform.googleapis.com`, and `discoveryengine.googleapis.com`, but ensure your account has permission to enable APIs (`roles/serviceusage.serviceUsageAdmin`).
> 2. **Cloud Build IAM Permissions**: The Cloud Build service account (`<PROJECT_NUMBER>@cloudbuild.gserviceaccount.com`) must have read/write access to Cloud Storage (`roles/storage.objectAdmin`) and Cloud Logging (`roles/logging.logWriter`). Our deployment scripts automatically grant these bindings.
> 3. **Synchronized Lockfile**: In a fresh environment, run `uv sync` before running deployment so `uv.lock` is synchronized with `pyproject.toml`.

### 4. Required IAM Roles & Out-of-Band IAM Provisioning

#### Developer Deployment Roles
The developer or CI/CD service principal running `./deploy-agent.sh` (or `.\deploy-agent.ps1` on Windows) must have:
- **Vertex AI User (`roles/aiplatform.user`)**: To deploy and update Vertex AI Reasoning Engine instances (`aiplatform.googleapis.com/ReasoningEngine`).
- **Service Account User (`roles/iam.serviceAccountUser`)**: To attach the Reasoning Engine service account (`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re...`) during deployment.
- **Storage Admin (`roles/storage.admin`)**: To create and manage the Google Cloud Storage bucket for video artifacts and deployment staging packages.
- **Service Usage Admin (`roles/serviceusage.serviceUsageAdmin`)**: To enable required Google Cloud APIs (`cloudbuild.googleapis.com`, `aiplatform.googleapis.com`, etc.).
- **Discovery Engine Editor (`roles/discoveryengine.editor`)**: To register and publish the agent to Gemini Enterprise (`discoveryengine.googleapis.com`).

#### What If You Don't Have Project IAM Admin Access?
Our deployment scripts attempt to grant required runtime IAM roles automatically. If you do not have **Project IAM Admin (`roles/resourcemanager.projectIamAdmin`)**, the script will catch the permission error, display a warning, and continue deploying without aborting.

Before deploying in a non-admin environment, ask your organization's Google Cloud Project IAM Admin to run our authoritative IAM setup script:
- **macOS / Linux**: `bash ./set-iam-permissions.sh`
- **Windows**: `.\set-iam-permissions.ps1`

#### Why Are Specific Runtime IAM Roles Required?
The IAM setup script binds the following required roles to the Reasoning Engine (`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) and Cloud Build (`<PROJECT_NUMBER>@cloudbuild.gserviceaccount.com`) service accounts:
1. **`roles/storage.objectAdmin` (Why not `objectViewer` or `objectCreator`?)**:
   - **Video Editing & Overwriting**: Omni-Agent generates video `.mp4` artifacts from `gemini-omni-flash-preview` and edits multi-scene storyboards. It must read, overwrite, create, and delete blobs in GCS (`$GCS_BUCKET_NAME`).
   - **Cloud Build Staging Cleanup**: During container builds, Cloud Build reads source tarballs from staging buckets and cleans up temporary blobs after build completion.
   - Less permission (`objectViewer` or `objectCreator`) prevents video version overwrites and breaks container staging cleanup.
2. **`roles/iam.serviceAccountTokenCreator` (Why is this needed?)**:
   - **V4 Signed URL Signing**: Reasoning Engine managed service accounts do not hold downloadable private key `.json` files. To allow users and web frontends to stream generated videos directly from GCS, Omni-Agent calls the IAM Credentials API (`signBlob`) to generate 7-day **V4 Signed URLs**. Calling `signBlob` on a service account requires `roles/iam.serviceAccountTokenCreator`.
3. **`roles/aiplatform.user`**: Allows Reasoning Engine to invoke Gemini Enterprise models (`gemini-3.5-flash`, `gemini-omni-flash-preview`).
4. **`roles/logging.logWriter`**: Allows Cloud Build to stream container build logs to Cloud Logging.

### 5. Deploy & Publish

Deploy the agent to Vertex AI Reasoning Engine and publish it to Gemini Enterprise using the authoritative deployment script:

**macOS / Linux (`bash`):**
```bash
bash ./deploy-agent.sh
```

**Windows (`PowerShell`):**
```powershell
.\deploy-agent.ps1
```

---

## Development & Local Testing

### Local Development IAM Permissions
When running local tests (`agents-cli run`, `agents-cli playground`, or integration tests), the agent executes under your default Compute Engine developer service account (`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`).

To grant the necessary runtime permissions (`roles/aiplatform.user`, `roles/storage.objectAdmin`, `roles/iam.serviceAccountTokenCreator`) for local development without affecting production deployment service accounts, run:
- **macOS / Linux**: `bash ./set-local-dev-permissions.sh`
- **Windows**: `.\set-local-dev-permissions.ps1`

### Running Local Tests
Install dependencies and run local unit/integration tests using `uv`:

```bash
# Run tests
uv run pytest tests/unit tests/integration

# Launch local playground
agents-cli playground
```
