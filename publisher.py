import subprocess
import os
import logging

logger = logging.getLogger(__name__)

def publish(text, image_path=None):
    """Публикует пост через официальный навык Binance Square."""
    skill_dir = find_skill_dir()
    if not skill_dir:
        logger.error("Skill directory not found.")
        return False

    api_key = os.getenv("SQUARE_API") or os.getenv("BINANCE_SQUARE_OPENAPI_KEY")
    if not api_key:
        logger.error("No API key.")
        return False

    env = os.environ.copy()
    env["BINANCE_SQUARE_OPENAPI_KEY"] = api_key.strip()

    if image_path and os.path.exists(image_path):
        script = os.path.join(skill_dir, "scripts", "post-image.mjs")
        cmd = ["node", script, "--text", text, "--images", image_path]
        logger.info(f"Running image post: {' '.join(cmd)}")
    else:
        script = os.path.join(skill_dir, "scripts", "post-text.mjs")
        cmd = ["node", script, "--text", text]
        logger.info(f"Running text post: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            cwd=skill_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=60
        )
        logger.info(f"STDOUT: {result.stdout}")
        if result.stderr:
            logger.warning(f"STDERR: {result.stderr}")
        if "Success!" in result.stdout or "Content ID" in result.stdout:
            return True
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Publication error: {e}")
        return False

def find_skill_dir():
    """Ищет директорию установленного навыка square-post."""
    base_paths = [
        os.getenv("GITHUB_WORKSPACE", "."),
        ".",
    ]
    for base in base_paths:
        candidate = os.path.join(base, ".agents", "skills", "square-post")
        if os.path.exists(os.path.join(candidate, "scripts", "post-image.mjs")):
            return candidate
    alt_paths = [
        os.path.expanduser("~/.agents/skills/square-post"),
        "./node_modules/@binance/square-post",
        "./skills/binance/square-post",
    ]
    for path in alt_paths:
        if os.path.exists(os.path.join(path, "scripts", "post-image.mjs")):
            return path
    return None