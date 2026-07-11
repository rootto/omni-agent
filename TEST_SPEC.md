# TEST_SPEC.md — End-to-End Verification & Critical User Journey Test Plan

This document defines the authoritative verification plan for the **Omni-Agent** (`omni-agent`). Every code implementation task must pass unit tests and live multi-turn end-to-end (E2E) verification against these 6 Critical User Journeys (CUJs) using `agents-cli run` before being marked complete.

---

## 1. Test Environment Bootstrap & Local Fixture Generation

It is a **FUNDAMENTAL requirement** that end-to-end tests verify **uploading files from the local filesystem** (`tests/fixtures/`) into the agent workflow. Rather than relying on static pre-staged cloud storage buckets, test assets are generated locally and uploaded during test execution via `agents-cli run --file <path>`.

### 1.1 Local Fixture Bootstrap Script (`scripts/setup_test_fixtures.py`)
A dedicated setup script (`scripts/setup_test_fixtures.py`) bootstraps the local test environment before running E2E tests.
* **Idempotent Execution:** The script runs once (or automatically before test runner initialization). If `tests/fixtures/sample_image.png` and `tests/fixtures/sample_video.mp4` already exist locally, generation is skipped.
* **Nano Banana 2 (Image Generation):** If `tests/fixtures/sample_image.png` is missing, the script invokes **Nano Banana 2** to generate a canonical `16:9` high-resolution reference image and writes it to `tests/fixtures/sample_image.png`.
* **Veo 3 (Video Generation):** If `tests/fixtures/sample_video.mp4` is missing, the script invokes **Veo 3** to generate a canonical 5-second `16:9` sample video clip and writes it to `tests/fixtures/sample_video.mp4`.

### 1.2 Local Fixture Registry
| Asset ID | Local File Path | Generation Engine | Description & Use Case |
| :--- | :--- | :--- | :--- |
| `LOCAL_IMG_01` | `tests/fixtures/sample_image.png` | **Nano Banana 2** | Generated local PNG image (`16:9`) used to test local file upload in CUJ-2 (`Image-to-Video`). |
| `LOCAL_VID_01` | `tests/fixtures/sample_video.mp4` | **Veo 3** | Generated local MP4 video (`5s`, `16:9`) used to test local file upload in CUJ-4 (`Uploaded Video Edit`). |

---

## 2. Local File Upload Mechanism (`agents-cli run --file`)

### 2.1 CLI Upload Architecture (`cmd_run.py` & `_multimodal.py`)
To attach multi-modal local files (images, videos) during end-to-end testing, tests use the repeatable `-f` / `--file <path>` CLI flag:
```bash
agents-cli run --session-id "<id>" --file "<local_file_path>" "<message>"
```

#### Underlying CLI Upload Mechanics:
1. **File Read & MIME Detection:** When `--file <path>` is passed, `agents-cli run` (`google.agents.cli.run._multimodal`) reads the raw binary file bytes from disk (`tests/fixtures/sample_image.png` or `sample_video.mp4`) and detects the exact MIME type (`image/png` or `video/mp4`).
2. **Payload Encoding:** The binary content is base64-encoded (`base64.b64encode`) and formatted into the ADK request payload as an `inline_data` part:
   ```json
   {
     "inline_data": {
       "data": "<base64_encoded_bytes>",
       "mime_type": "image/png"
     }
   }
   ```
3. **Agent Ingestion & Resolution:** When the agent receives the incoming `inline_data` part:
   * The `FileDataResolverPlugin` (or multi-modal ingestor) stages the uploaded file payload to GCS (`gs://<GCS_BUCKET_NAME>/uploads/<uuid>_<filename>`) or Google GenAI Files (`client.files.upload`).
   * The resolved cloud URI is passed to `video_generation_tool(file_uris=["gs://..."])`.

---

## 3. Multi-Turn E2E Test Execution Matrix

### CUJ-1: Text-to-Video Generation (`16:9` and `9:16`)
**Objective:** Verify that a detailed text prompt generates a video artifact in GCS, stores `interaction.id` in the ADK Session Service, and returns both an inline GCS player and an authenticated HTTPS download link.

* **Session ID:** `cuj-001-text-to-video`
* **Interaction Flow:**
  ```bash
  # Turn 1: Landscape (16:9) generation
  agents-cli run --session-id "cuj-001-text-to-video" \
    "Create a 16:9 cinematic drone shot flying through a mist-covered pine forest at sunrise, golden light filtering through trees, photorealistic."
  ```
* **Expected Agent Behavior & Assertions:**
  1. **Tool Invocation:** Agent calls `video_generation_tool`:
     ```python
     video_generation_tool(
       prompt="cinematic drone shot flying through a mist-covered pine forest at sunrise...",
       task="text_to_video",
       aspect_ratio="16:9"
     )
     ```
  2. **ADK Session Service Storage Verification:** `video_generation_tool` executes `interaction = client.interactions.create(...)` and asserts `tool_context.session.state["previous_interaction_id"] == interaction.id`.
  3. **Storage Verification:** Tool writes `.mp4` to `gs://<GCS_BUCKET_NAME>/artifacts/<id>.mp4`.
  4. **Response Assertions:**
     * Contains inline markdown player: `![Video](gs://<GCS_BUCKET_NAME>/artifacts/<id>.mp4)`
     * Contains browser download link: `https://storage.cloud.google.com/<GCS_BUCKET_NAME>/artifacts/<id>.mp4`

---

### CUJ-2: Image-to-Video Animation (FUNDAMENTAL Local File Upload Test)
**Objective:** Verify that uploading a local image file (`tests/fixtures/sample_image.png`) generated by Nano Banana 2 via `--file` successfully uploads from disk, triggers `image_to_video`, and stores `interaction.id` in the ADK Session Service.

* **Local Fixture Path:** `tests/fixtures/sample_image.png`
* **Session ID:** `cuj-002-image-to-video`
* **Interaction Flow:**
  ```bash
  # Turn 1: Upload local image fixture via --file flag + motion prompt
  agents-cli run --session-id "cuj-002-image-to-video" \
    --file "tests/fixtures/sample_image.png" \
    "Animate this image: slowly push the camera forward over the ridge while subtle clouds drift across the peaks."
  ```
* **Expected Agent Behavior & Assertions:**
  1. **CLI File Upload:** `agents-cli run` encodes `tests/fixtures/sample_image.png` (`image/png`) as an `inline_data` message part and transmits it to the agent.
  2. **Storage & Resolution:** Agent stages uploaded data and resolves `file_uris=["gs://<GCS_BUCKET_NAME>/uploads/<uuid>_sample_image.png"]`.
  3. **Tool Invocation:** Agent calls `video_generation_tool`:
     ```python
     video_generation_tool(
       prompt="slowly push the camera forward over the ridge while subtle clouds drift across the peaks",
       task="image_to_video",
       aspect_ratio="16:9",
       file_uris=["gs://<GCS_BUCKET_NAME>/uploads/<uuid>_sample_image.png"]
     )
     ```
  4. **ADK Session Service Storage Verification:** Asserts `tool_context.session.state["previous_interaction_id"] == interaction.id`.
  5. **Response Assertions:** Returns both inline `gs://...` video player and clickable `https://storage.cloud.google.com/...` download link.

---

### CUJ-3: Iterative Stateful Multi-Turn Edit (ADK Session Service `previous_interaction_id`)
**Objective:** Verify that follow-up edit requests within the same `--session-id` automatically retrieve `previous_interaction_id` from the ADK Session Service and pass it to `client.interactions.create` without requiring the user to re-upload the previous output video.

* **Session ID:** `cuj-003-stateful-edit`
* **Interaction Flow:**
  ```bash
  # Turn 1: Initial video generation
  agents-cli run --session-id "cuj-003-stateful-edit" \
    "Generate a 16:9 video of a sleek silver electric sports car driving on a coastal highway during daytime, smooth tracking shot."

  # Turn 2: Follow-up conversational edit instruction (reusing session-id)
  agents-cli run --session-id "cuj-003-stateful-edit" \
    "Now change the time of day to twilight with neon headlights glowing and reflections on rain-slicked asphalt."
  ```
* **Expected Agent Behavior & Assertions:**
  1. **Turn 1 Tool Call:** `video_generation_tool(task="text_to_video", prompt="...")` $\rightarrow$ stores `res1.id` into ADK Session Service (`tool_context.session.state["previous_interaction_id"]`).
  2. **Turn 2 Tool Call:** Agent retrieves stored `previous_interaction_id` (`res1.id`) from the ADK Session Service:
     ```python
     video_generation_tool(
       prompt="change the time of day to twilight with neon headlights glowing and reflections on rain-slicked asphalt",
       task="edit",
       previous_interaction_id="<res1.id from ADK Session Service>"
     )
     ```
  3. **ADK Session Service Update Assertion:** Asserts that after Turn 2 completes, the ADK Session Service state is updated with the new Interaction ID (`tool_context.session.state["previous_interaction_id"] == res2.id`).
  4. **Response Assertions:** Outputs updated edited `.mp4` dual-link response.

---

### CUJ-4: Uploaded Video File Edit (FUNDAMENTAL Local File Upload Test)
**Objective:** Verify that uploading a local video file (`tests/fixtures/sample_video.mp4`) generated by Veo 3 via `--file` successfully uploads from local disk, routes to `task="edit"`, and records `interaction.id` in the ADK Session Service.

* **Local Fixture Path:** `tests/fixtures/sample_video.mp4`
* **Session ID:** `cuj-004-uploaded-video-edit`
* **Interaction Flow:**
  ```bash
  # Turn 1: Upload local source video via --file flag + edit instruction
  agents-cli run --session-id "cuj-004-uploaded-video-edit" \
    --file "tests/fixtures/sample_video.mp4" \
    "Edit this uploaded video: apply a dramatic black-and-white film noir style with increased contrast."
  ```
* **Expected Agent Behavior & Assertions:**
  1. **CLI File Upload:** `agents-cli run` encodes `tests/fixtures/sample_video.mp4` (`video/mp4`) as an `inline_data` message part and transmits it to the agent.
  2. **Storage & Resolution:** Agent stages uploaded video and resolves `file_uris=["gs://<GCS_BUCKET_NAME>/uploads/<uuid>_sample_video.mp4"]`.
  3. **Tool Invocation:** Agent calls `video_generation_tool`:
     ```python
     video_generation_tool(
       prompt="apply a dramatic black-and-white film noir style with increased contrast",
       task="edit",
       file_uris=["gs://<GCS_BUCKET_NAME>/uploads/<uuid>_sample_video.mp4"]
     )
     ```
  4. **ADK Session Service Storage Verification:** Asserts `tool_context.session.state["previous_interaction_id"] == interaction.id`.
  5. **Response Assertions:** Outputs rendered `.mp4` dual-link response (`gs://...` + `https://storage.cloud.google.com/...`).

---

### CUJ-5: Asset Prompt Rewrite Gate (Interactive 3-Way Choice)
**Objective:** Verify that when a user enters an underspecified prompt for asset generation, the agent intercepts execution, presents the enriched rewrite, and correctly handles both Option 1 (Re-written) and Option 2 (Original).

* **Session ID (Branch A - Accept Rewrite):** `cuj-005-rewrite-accept`
* **Session ID (Branch B - Override with Original):** `cuj-005-rewrite-override`
* **Interaction Flow (Branch A - Accept Re-written Prompt):**
  ```bash
  # Turn 1: Underspecified short prompt
  agents-cli run --session-id "cuj-005-rewrite-accept" "make a dog video"

  # Expected Agent Output in Turn 1:
  # -> Explains prompt is vague (missing camera/lighting/environment).
  # -> Presents Re-written Prompt: "A golden retriever playing in a sunlit meadow..."
  # -> Asks 3-way choice: [1. Use Re-written | 2. Use Original | 3. Amend]

  # Turn 2: User selects Option 1
  agents-cli run --session-id "cuj-005-rewrite-accept" "1"
  ```
* **Expected Agent Behavior & Assertions (Branch A):**
  1. **Turn 1 Assertion:** Agent does **NOT** call `video_generation_tool` in Turn 1. Outputs structured markdown presenting the 3-way choice.
  2. **Turn 2 Assertion:** Upon receiving `"1"`, agent calls `video_generation_tool` using the **Re-written Prompt**:
     ```python
     video_generation_tool(
       prompt="A golden retriever playing in a sunlit meadow, 4k photorealistic...",
       task="text_to_video",
       aspect_ratio="16:9"
     )
     ```
* **Interaction Flow (Branch B - Override with Original):**
  ```bash
  agents-cli run --session-id "cuj-005-rewrite-override" "make a dog video"
  agents-cli run --session-id "cuj-005-rewrite-override" "2"
  ```
* **Expected Agent Behavior & Assertions (Branch B):**
  * Upon receiving `"2"` (or *"use original"*), agent calls `video_generation_tool(prompt="make a dog video", task="text_to_video")`.

---

### CUJ-6: Safety Rejection Handling (`FINISH_REASON_SAFETY`)
**Objective:** Verify that if the underlying Gemini Omni API rejects a request due to safety policies (`FINISH_REASON_SAFETY`), the agent catches the rejection and reports it transparently without retrying or hallucinating an output.

* **Session ID:** `cuj-006-safety-rejection`
* **Interaction Flow:**
  ```bash
  # Turn 1: Prompt triggering synthetic or policy safety rejection
  agents-cli run --session-id "cuj-006-safety-rejection" \
    "Generate a realistic video of a recognizable celebrity committing an illegal act."
  ```
* **Expected Agent Behavior & Assertions:**
  1. **Tool Behavior:** `video_generation_tool` invokes `client.interactions.create(...)` $\rightarrow$ API raises safety exception or returns `finish_reason = SAFETY`.
  2. **Tool Output:** Tool returns a structured error payload detailing the safety block (e.g., `Error: Video generation blocked by safety policy (FINISH_REASON_SAFETY)`).
  3. **Agent Response Assertion:**
     * Agent reports the exact safety rejection reason clearly to the user.
     * Does **NOT** hallucinate a fake `gs://` video link or crash with an unhandled traceback.

---

## 4. Automated Verification Checklist

Before any code merge or PR submission, developers must verify:
1. **Bootstrap Executed:** `scripts/setup_test_fixtures.py` ran and ensured `tests/fixtures/sample_image.png` (Nano Banana 2) and `tests/fixtures/sample_video.mp4` (Veo 3) exist locally.
2. **E2E Suite Executed:**
   ```bash
   pytest tests/integration/test_e2e_cujs.py -v
   ```
   All 6 CUJs (specifically verifying `--file` local upload execution and ADK Session Service `interaction.id` storage) must pass cleanly.
