import subprocess
import os

def publish(text, image_path=None):
    skill_dir = find_skill_dir()
    if not skill_dir:
        print("[PUBLISH] Skill not found.")
        return False

    api_key = os.getenv("SQUARE_API") or os.getenv("BINANCE_SQUARE_OPENAPI_KEY")
    if api_key:
        api_key = api_key.strip()
    else:
        print("[PUBLISH] No API key.")
        return False

    env = os.environ.copy()
    env["BINANCE_SQUARE_OPENAPI_KEY"] = api_key

    if image_path and os.path.exists(image_path):
        script = os.path.join(skill_dir, "scripts", "post-image.mjs")
        cmd = ["node", script, "--text", text, "--images", image_path]
        print(f"[PUBLISH] Running: {' '.join(cmd)}")
    else:
        script = os.path.join(skill_dir, "scripts", "post-text.mjs")
        cmd = ["node", script, "--text", text]
        print(f"[PUBLISH] Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            cwd=skill_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=60
        )
        print("[PUBLISH] STDOUT:", result.stdout)
        if result.stderr:
            print("[PUBLISH] STDERR:", result.stderr)
        if "Success!" in result.stdout or "Content ID" in result.stdout:
            return True
        return result.returncode == 0
    except Exception as e:
        print(f"[PUBLISH] ERROR: {e}")
        return False


def find_skill_dir():
    # Пути, где может находиться установленный навык
    possible_paths = [
        os.path.join(os.getenv("GITHUB_WORKSPACE", "."), ".agents", "skills", "square-post"),
        os.path.expanduser("~/.agents/skills/square-post"),
        "./.agents/skills/square-post",
        "./node_modules/@binance/square-post",
        "./skills/binance/square-post",
    ]
    for d in possible_paths:
        if os.path.exists(os.path.join(d, "scripts", "post-image.mjs")):
            return d
    return None
