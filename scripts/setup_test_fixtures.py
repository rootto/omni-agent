#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Bootstrap script to generate real, valid, playable local test fixtures (sample_image.png and sample_video.mp4)."""

import logging
import subprocess
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def ensure_test_fixtures(force: bool = False) -> None:
    """Generates real, playable 16:9 local test fixtures using ffmpeg lavfi if missing or forced."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    img_path = FIXTURES_DIR / "sample_image.png"
    if not force and img_path.exists() and img_path.stat().st_size > 1000:
        logger.info("Playable local fixture %s already exists.", img_path)
    else:
        logger.info("Generating canonical 1280x720 test PNG image fixture at %s...", img_path)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=1:size=1280x720",
                "-vframes",
                "1",
                "-update",
                "1",
                str(img_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Generated canonical image fixture (%d bytes)", img_path.stat().st_size)

    vid_path = FIXTURES_DIR / "sample_video.mp4"
    if not force and vid_path.exists() and vid_path.stat().st_size > 10000:
        logger.info("Playable local fixture %s already exists.", vid_path)
    else:
        logger.info("Generating canonical 5s 1280x720 playable MP4 test video fixture at %s...", vid_path)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=5:size=1280x720:rate=30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(vid_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Generated canonical video fixture (%d bytes)", vid_path.stat().st_size)


if __name__ == "__main__":
    ensure_test_fixtures(force=True)
