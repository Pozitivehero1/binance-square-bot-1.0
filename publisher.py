"""Publish text or image posts through the installed Binance Square skill."""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Union

logger = logging.getLogger(__name__)

ImageInput = Optional[Union[str, Iterable[str]]]


def _existing_images(image_path: ImageInput) -> List[str]:
    if image_path is None:
        return []
    candidates = [image_path] if isinstance(image_path, str) else list(image_path)
    result = []
    for candidate in candidates:
        path = Path(str(candidate)).expanduser().resolve()
        if path.is_file():
            result.append(str(path))
        else:
            logger.warning("Skipping missing image: %s", candidate)
    return result


def publish(text: str, image_path: ImageInput = None) -> bool:
    if not text or not text.strip():
        logger.error("Refusing to publish an empty post")
        return False

    skill_dir = find_skill_dir()
    if not skill_dir:
        logger.error("Binance Square skill directory not found")
        return False

    api_key = (os.getenv("SQUARE_API") or os.getenv("BINANCE_SQUARE_OPENAPI_KEY") or "").strip()
    if not api_key:
        logger.error("No Binance Square API key configured")
        return False

    environment = os.environ.copy()
    environment["BINANCE_SQUARE_OPENAPI_KEY"] = api_key
    images = _existing_images(image_path)

    if images:
        script = Path(skill_dir) / "scripts" / "post-image.mjs"
        command = ["node", str(script), "--text", text, "--images", ",".join(images)]
        logger.info("Publishing image post (%s chars, %s image(s))", len(text), len(images))
    else:
        script = Path(skill_dir) / "scripts" / "post-text.mjs"
        command = ["node", str(script), "--text", text]
        logger.info("Publishing text post (%s chars)", len(text))

    if not script.is_file():
        logger.error("Publish script does not exist: %s", script)
        return False

    try:
        result = subprocess.run(
            command,
            cwd=skill_dir,
            env=environment,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("PUBLISH_TIMEOUT", "90")),
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.error("Binance Square publication timed out")
        return False
    except (OSError, ValueError) as exc:
        logger.error("Publication process failed: %s", exc)
        return False

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if stdout:
        logger.info("Publisher output: %s", stdout[:1200])
    if stderr:
        logger.warning("Publisher stderr: %s", stderr[:1200])

    success_markers = ("Success!", "Content ID", "contentId", "postId")
    if result.returncode == 0 and any(marker.lower() in stdout.lower() for marker in success_markers):
        return True
    if result.returncode == 0 and not any(word in stdout.lower() for word in ("error", "failed", "exception")):
        return True

    logger.error("Publication rejected (exit code %s)", result.returncode)
    return False


def find_skill_dir() -> Optional[str]:
    roots = [
        Path(os.getenv("GITHUB_WORKSPACE", ".")),
        Path.cwd(),
        Path.home(),
    ]
    candidates = []
    for root in roots:
        candidates.extend(
            [
                root / ".agents" / "skills" / "square-post",
                root / "node_modules" / "@binance" / "square-post",
                root / "skills" / "binance" / "square-post",
            ]
        )

    seen = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        scripts = resolved / "scripts"
        if (scripts / "post-text.mjs").is_file() or (scripts / "post-image.mjs").is_file():
            return str(resolved)
    return None
