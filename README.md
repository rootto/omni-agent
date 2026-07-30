# Omni-Agent

Omni-Agent is an AI-powered conversational video generation and editing assistant built on top of **Google's Gemini Omni** (`gemini-omni-flash-preview`), executing high-speed cinematic video creation and editing through Vertex AI Reasoning Engine and Gemini Enterprise.

---

## Quick Start

### 1. Install Prerequisites (`uv` & `uvx`)

This project uses **uv** for fast Python package and tool management. Installing `uv` automatically installs the `uvx` tool runner:

```bash
# Install uv and uvx (macOS / Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or install via Homebrew
brew install uv

# Install Google Agents CLI via uvx
uvx google-agents-cli setup
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and configure your target environment variables:

```bash
cp .env.example .env
```

Required variables in `.env`:
- `GOOGLE_CLOUD_PROJECT`: Your Google Cloud Project ID (e.g. `omini-test-agent`).
- `GOOGLE_CLOUD_PROJECT_NUMBER`: Your numeric Google Cloud Project Number (e.g. `687484203981`).
- `GEMINI_ENTERPRISE_INSTANCE`: Your target Gemini Enterprise engine instance name.
- `GCS_BUCKET_NAME` & `LOGS_BUCKET_NAME`: Google Cloud Storage bucket name for video artifacts and logs.
- `GOOGLE_GENAI_USE_ENTERPRISE`: Set to `1` to use Vertex AI / Enterprise endpoints.
- `AGENT_MODEL_ID`: Orchestration model (default: `gemini-3.5-flash`).
- `OMNI_MODEL_ID`: Video generation model (default: `gemini-omni-flash-preview`).

### 3. Required IAM Roles for Deployment

The user or service principal running `./deploy-agent.sh` must have the following Google Cloud IAM roles on the target project (`GOOGLE_CLOUD_PROJECT`):

- **Project IAM Admin (`roles/resourcemanager.projectIamAdmin`)**: To bind runtime IAM roles (`aiplatform.user`, `storage.objectAdmin`, `iam.serviceAccountTokenCreator`) to the Reasoning Engine service accounts.
- **Vertex AI Admin (`roles/aiplatform.admin`)** *or* **Vertex AI User (`roles/aiplatform.user`)**: To create and update Vertex AI Reasoning Engine instances (`aiplatform.googleapis.com/ReasoningEngine`).
- **Service Account User (`roles/iam.serviceAccountUser`)**: To attach the runtime service account (`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re...`) during deployment.
- **Storage Admin (`roles/storage.admin`)**: To create and manage the Google Cloud Storage bucket for video artifacts and deployment staging packages.
- **Service Usage Admin (`roles/serviceusage.serviceUsageAdmin`)**: To enable required Google Cloud APIs (`cloudresourcemanager.googleapis.com`, `aiplatform.googleapis.com`).
- **Discovery Engine Editor (`roles/discoveryengine.editor`)** *or* **Discovery Engine Admin (`roles/discoveryengine.admin`)**: To register and publish the agent to Gemini Enterprise (`discoveryengine.googleapis.com`).

### 4. Deploy & Publish

Deploy the agent to Vertex AI Reasoning Engine and publish it to Gemini Enterprise using the authoritative deployment script:

```bash
bash ./deploy-agent.sh
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
