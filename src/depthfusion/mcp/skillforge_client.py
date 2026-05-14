"""HTTP client for SkillForge draft endpoint (E-34 S-109).

URL and API key are always read from environment variables:
  DEPTHFUSION_SKILLFORGE_URL     — base URL of SkillForge instance
  DEPTHFUSION_SKILLFORGE_API_KEY — bearer token for SkillForge API

When DEPTHFUSION_SKILLFORGE_URL is unset, all calls are no-ops and return None.
Retries 3 attempts with exponential backoff (1s, 2s, 4s). Failures are logged,
never raised to callers.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


def post_skill_draft(
    name: str,
    description: str,
    pattern_key: str,
    session_count: int,
) -> dict[str, Any] | None:
    """POST a candidate skill draft to SkillForge.

    Returns the parsed JSON response dict on success, None on failure or
    when SkillForge is not configured.
    """
    url = os.getenv("DEPTHFUSION_SKILLFORGE_URL", "").rstrip("/")
    api_key = os.getenv("DEPTHFUSION_SKILLFORGE_API_KEY", "")

    if not url:
        logger.debug("DEPTHFUSION_SKILLFORGE_URL not set; skipping SkillForge draft for %s", pattern_key)
        return None

    endpoint = f"{url}/skills/draft"
    payload = json.dumps(
        {
            "name": name,
            "description": description,
            "pattern_key": pattern_key,
            "session_count": session_count,
            "source": "depthfusion",
        }
    ).encode()

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode()
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            logger.warning(
                "SkillForge draft POST attempt %d/%d failed: HTTP %s for %s",
                attempt, _MAX_ATTEMPTS, exc.code, pattern_key,
            )
        except Exception as exc:
            logger.warning(
                "SkillForge draft POST attempt %d/%d error: %s for %s",
                attempt, _MAX_ATTEMPTS, exc, pattern_key,
            )

        if attempt < _MAX_ATTEMPTS:
            time.sleep(2 ** (attempt - 1))  # 1s, 2s

    logger.error(
        "SkillForge draft POST failed after %d attempts for pattern_key=%s",
        _MAX_ATTEMPTS, pattern_key,
    )
    return None
