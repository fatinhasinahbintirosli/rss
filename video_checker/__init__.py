"""Pre-upload checker for Facebook videos (Postolab).

Layers:
  1. technical  - format/size/duration/resolution vs Facebook API limits
  2. copyright  - music fingerprinting (AudD or ACRCloud)
  3. visual     - frame moderation (Claude vision or AWS Rekognition)
  4. text       - transcript + on-screen text + caption vs policy rules and an LLM review

No checker can guarantee a video will never be actioned by Facebook. The output
is a traffic-light verdict (green / yellow / red) with reasons and timestamps.
"""

__version__ = "0.1.0"

from .pipeline import check_video  # noqa: E402,F401
