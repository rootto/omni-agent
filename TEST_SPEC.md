# TEST_SPEC.md — End-to-End Verification & Critical User Journey Test Plan

This document defines the authoritative verification plan for the **Omni-Agent** (`omni-agent`). Every code implementation task must pass unit tests and live multi-turn end-to-end (E2E) verification against these 6 Critical User Journeys (CUJs) using `agent-cli run` before being marked complete.

---

## 1. Test Fixtures & Asset Sourcing

To verify journeys that require existing images or source videos (`Image-to-Video` and `Video-to-Video Edit`), canonical test fixtures must be staged in the project's Google Cloud Storage (GCS) test bucket (`gs://<GCS_BUCKET_NAME>/test_fixtures/`) prior to E2E test execution.

### 1.1 Standard Test Fixture Registry
| Asset ID | Fixture Type | GCS Staging URI | Description & Use Case |
| :--- | :--- | :--- | :--- |
| `TEST_IMG_01` | PNG Image (`16:9`) | `gs://<GCS_BUCKET_NAME>/test_fixtures/sample_landscape_1080p.png` | High-resolution landscape image (mountain sunset) used for CUJ-2 (`Image-to-Video`). |
| `TEST_VID_01` | MP4 Video (`5s`, `16:9`) | `gs://<GCS_BUCKET_NAME>/test_fixtures/sample_input_video_5s.mp4` | Canonical 5-second source video clip used for CUJ-4 (`Uploaded Video Edit`). |

### 1.2 Local Dev & CLI Asset Ingestion
When running multi-turn verification via `agent-cli run`:
* For multi-modal file inputs (`FileData` simulation in CLI), the test runner passes either:
  * An explicit reference to the GCS fixture URI (`gs://<GCS_BUCKET_NAME>/test_fixtures/...`).
  * Or simulates Gemini Enterprise `FileData` upload resolution via the `FileDataResolverPlugin` which maps filenames (`sample_landscape_1080p.png`) to the underlying `gs://` URI.

---

## 2. Multi-Turn E2E Test Execution Matrix

### CUJ-1: Text-to-Video Generation (`16:9` and `9:16`)
**Objective:** Verify that a detailed text prompt generates a video artifact in GCS and returns both an inline GCS player and an authenticated HTTPS download link.

* **Session ID:** `cuj-001-text-to-video`
* **Interaction Flow:**
  ```bash
  # Turn 1: Landscape (16:9) generation
  agent-cli run --session-id "cuj-001-text-to-video" \
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
  2. **Storage Verification:** Tool writes `.mp4` to `gs://<GCS_BUCKET_NAME>/artifacts/<id>.mp4`.
  3. **Response Assertions:**
     * Contains inline markdown player: `![Video](gs://<GCS_BUCKET_NAME>/artifacts/<id>.mp4)`
     * Contains browser download link: `https://storage.cloud.google.com/<GCS_BUCKET_NAME>/artifacts/<id>.mp4`
  4. **State Assertion:** Session state records `previous_interaction_id = <interaction.id>`.

---

### CUJ-2: Image-to-Video Animation (Using Staged GCS Test Image)
**Objective:** Verify that an uploaded reference image (`TEST_IMG_01`) combined with a motion prompt successfully triggers `image_to_video`.

* **Asset Source:** `gs://<GCS_BUCKET_NAME>/test_fixtures/sample_landscape_1080p.png`
* **Session ID:** `cuj-002-image-to-video`
* **Interaction Flow:**
  ```bash
  # Turn 1: Pass image fixture URI + motion instruction
  agent-cli run --session-id "cuj-002-image-to-video" \
    "Using the reference image gs://<GCS_BUCKET_NAME>/test_fixtures/sample_landscape_1080p.png, animate the camera slowly pushing forward over the ridge while subtle clouds drift across the peaks."
  ```
* **Expected Agent Behavior & Assertions:**
  1. **URI Resolution:** `FileDataResolverPlugin` (or Main Agent) resolves `file_uris=["gs://<GCS_BUCKET_NAME>/test_fixtures/sample_landscape_1080p.png"]`.
  2. **Tool Invocation:** Agent calls `video_generation_tool`:
     ```python
     video_generation_tool(
       prompt="animate the camera slowly pushing forward over the ridge while subtle clouds drift across the peaks",
       task="image_to_video",
       aspect_ratio="16:9",
       file_uris=["gs://<GCS_BUCKET_NAME>/test_fixtures/sample_landscape_1080p.png"]
     )
     ```
  3. **Response Assertions:** Returns both inline `gs://...` video player and clickable `https://storage.cloud.google.com/...` download link.

---

### CUJ-3: Iterative Stateful Multi-Turn Edit (`previous_interaction_id`)
**Objective:** Verify that follow-up edit requests within the same `--session-id` correctly retain and pass `previous_interaction_id` without requiring the user to re-upload the previous output video.

* **Session ID:** `cuj-003-stateful-edit`
* **Interaction Flow:**
  ```bash
  # Turn 1: Initial video generation
  agent-cli run --session-id "cuj-003-stateful-edit" \
    "Generate a 16:9 video of a sleek silver electric sports car driving on a coastal highway during daytime, smooth tracking shot."

  # Turn 2: Follow-up conversational edit instruction (reusing session-id)
  agent-cli run --session-id "cuj-003-stateful-edit" \
    "Now change the time of day to twilight with neon headlights glowing and reflections on rain-slicked asphalt."
  ```
* **Expected Agent Behavior & Assertions:**
  1. **Turn 1 Tool Call:** `video_generation_tool(task="text_to_video", prompt="...")` $\rightarrow$ records `res1.id` into session state `previous_interaction_id`.
  2. **Turn 2 Tool Call:** Agent detects follow-up edit intent and reuses stored `previous_interaction_id`:
     ```python
     video_generation_tool(
       prompt="change the time of day to twilight with neon headlights glowing and reflections on rain-slicked asphalt",
       task="edit",
       previous_interaction_id="<res1.id from Turn 1>"
     )
     ```
  3. **Response Assertions:** Outputs updated edited `.mp4` dual-link response and updates `previous_interaction_id` to `<res2.id>`.

---

### CUJ-4: Uploaded Video File Edit (`Video-to-Video`)
**Objective:** Verify that when a user uploads an external source `.mp4` file (`TEST_VID_01`) and requests modifications, the agent routes to `task="edit"` passing the source GCS URI.

* **Asset Source:** `gs://<GCS_BUCKET_NAME>/test_fixtures/sample_input_video_5s.mp4`
* **Session ID:** `cuj-004-uploaded-video-edit`
* **Interaction Flow:**
  ```bash
  # Turn 1: Reference uploaded source video + edit modification instruction
  agent-cli run --session-id "cuj-004-uploaded-video-edit" \
    "Edit this video gs://<GCS_BUCKET_NAME>/test_fixtures/sample_input_video_5s.mp4: apply a dramatic black-and-white film noir style with increased contrast."
  ```
* **Expected Agent Behavior & Assertions:**
  1. **URI Resolution:** Identifies source video URI `gs://<GCS_BUCKET_NAME>/test_fixtures/sample_input_video_5s.mp4`.
  2. **Tool Invocation:** Agent calls `video_generation_tool`:
     ```python
     video_generation_tool(
       prompt="apply a dramatic black-and-white film noir style with increased contrast",
       task="edit",
       file_uris=["gs://<GCS_BUCKET_NAME>/test_fixtures/sample_input_video_5s.mp4"]
     )
     ```
  3. **Response Assertions:** Outputs rendered `.mp4` dual-link response (`gs://...` + `https://storage.cloud.google.com/...`).

---

### CUJ-5: Asset Prompt Rewrite Gate (Interactive 3-Way Choice)
**Objective:** Verify that when a user enters an underspecified prompt for asset generation, the agent intercepts execution, presents the enriched rewrite, and correctly handles both Option 1 (Re-written) and Option 2 (Original).

* **Session ID (Branch A - Accept Rewrite):** `cuj-005-rewrite-accept`
* **Session ID (Branch B - Override with Original):** `cuj-005-rewrite-override`
* **Interaction Flow (Branch A - Accept Re-written Prompt):**
  ```bash
  # Turn 1: Underspecified short prompt
  agent-cli run --session-id "cuj-005-rewrite-accept" "make a dog video"

  # Expected Agent Output in Turn 1:
  # -> Explains prompt is vague (missing camera/lighting/environment).
  # -> Presents Re-written Prompt: "A golden retriever playing in a sunlit meadow..."
  # -> Asks 3-way choice: [1. Use Re-written | 2. Use Original | 3. Amend]

  # Turn 2: User selects Option 1
  agent-cli run --session-id "cuj-005-rewrite-accept" "1"
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
  agent-cli run --session-id "cuj-005-rewrite-override" "make a dog video"
  agent-cli run --session-id "cuj-005-rewrite-override" "2"
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
  agent-cli run --session-id "cuj-006-safety-rejection" \
    "Generate a realistic video of a recognizable celebrity committing an illegal act."
  ```
* **Expected Agent Behavior & Assertions:**
  1. **Tool Behavior:** `video_generation_tool` invokes `client.interactions.create(...)` $\rightarrow$ API raises safety exception or returns `finish_reason = SAFETY`.
  2. **Tool Output:** Tool returns a structured error payload detailing the safety block (e.g., `Error: Video generation blocked by safety policy (FINISH_REASON_SAFETY)`).
  3. **Agent Response Assertion:**
     * Agent reports the exact safety rejection reason clearly to the user.
     * Does **NOT** hallucinate a fake `gs://` video link or crash with an unhandled traceback.

---

## 3. Automated Verification Checklist

Before any code merge or PR submission, developers must run:
```bash
# Execute automated E2E test suite across all 6 CUJs
pytest tests/integration/test_e2e_cujs.py -v
```
All 6 CUJs must pass cleanly.
