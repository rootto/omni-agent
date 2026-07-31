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

### 4. Required IAM Roles for Deployment

The user or service principal running `./deploy-agent.sh` (or `.\deploy-agent.ps1` on Windows) must have the following Google Cloud IAM roles on the target project (`GOOGLE_CLOUD_PROJECT`):

- **Project IAM Admin (`roles/resourcemanager.projectIamAdmin`)**: To bind runtime IAM roles (`aiplatform.user`, `storage.objectAdmin`, `iam.serviceAccountTokenCreator`) to the Reasoning Engine service accounts.
- **Vertex AI Admin (`roles/aiplatform.admin`)** *or* **Vertex AI User (`roles/aiplatform.user`)**: To create and update Vertex AI Reasoning Engine instances (`aiplatform.googleapis.com/ReasoningEngine`).
- **Service Account User (`roles/iam.serviceAccountUser`)**: To attach the runtime service account (`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re...`) during deployment.
- **Storage Admin (`roles/storage.admin`)**: To create and manage the Google Cloud Storage bucket for video artifacts and deployment staging packages.
- **Service Usage Admin (`roles/serviceusage.serviceUsageAdmin`)**: To enable required Google Cloud APIs (`cloudresourcemanager.googleapis.com`, `aiplatform.googleapis.com`).
- **Discovery Engine Editor (`roles/discoveryengine.editor`)** *or* **Discovery Engine Admin (`roles/discoveryengine.admin`)**: To register and publish the agent to Gemini Enterprise (`discoveryengine.googleapis.com`).

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

Install dependencies and run local unit/integration tests using `uv`:

```bash
# Run tests
uv run pytest tests/unit tests/integration

# Launch local playground
agents-cli playground
```
