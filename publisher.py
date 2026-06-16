import subprocess
import os
import tempfile
import requests
import glob

def publish(text, image_path=None):
    """
    Публикует пост. Сначала пытается через навык (с изображением), затем через прямой API.
    """
    script_path = find_skill_script()
    if script_path:
        print(f"[PUBLISH] Found skill at: {script_path}")
        skill_dir = os.path.dirname(os.path.dirname(script_path))
        return publish_with_skill(text, image_path, script_path, skill_dir)
    else:
        print("[PUBLISH] Skill not found, using direct API (text only).")
        return publish_direct_api(text)


def find_skill_script():
    """Ищет post-text.mjs в стандартных местах установки навыков."""
    # 1. Самый вероятный путь (из логов установки)
    primary = os.path.expanduser("~/.agents/skills/square-post/scripts/post-text.mjs")
    if os.path.exists(primary):
        return primary

    # 2. Другие возможные пути
    patterns = [
        os.path.expanduser("~/.agents/skills/*/scripts/post-text.mjs"),
        os.path.expanduser("~/.skills/skills/*/scripts/post-text.mjs"),
        "./node_modules/@binance/*/scripts/post-text.mjs",
        "./skills/*/scripts/post-text.mjs",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]

    # 3. Если ничего не найдено, выведем отладку
    print("[DEBUG] No skill script found. Checked:")
    for p in [primary] + patterns:
        print(f"  - {p}")
    return None


def publish_with_skill(text, image_path, script_path, skill_dir):
    """Публикация через официальный навык Binance Square."""
    api_key = os.getenv("SQUARE_API") or os.getenv("BINANCE_SQUARE_OPENAPI_KEY")
    if api_key:
        api_key = api_key.strip()
    else:
        print("[PUBLISH] No API key, falling back to direct API.")
        return publish_direct_api(text)

    env = os.environ.copy()
    env["BINANCE_SQUARE_OPENAPI_KEY"] = api_key

    # Если есть изображение и навык поддерживает изображения
    if image_path and os.path.exists(image_path):
        image_script = script_path.replace("post-text.mjs", "post-image.mjs")
        if os.path.exists(image_script):
            cmd = ["node", image_script, "--text", text, "--images", image_path]
            print(f"[PUBLISH] Trying image post: {' '.join(cmd)}")
            result = subprocess.run(cmd, cwd=skill_dir, env=env,
                                   capture_output=True, text=True, timeout=60)
            print("[PUBLISH] STDOUT:", result.stdout)
            if result.stderr:
                print("[PUBLISH] STDERR:", result.stderr)
            if "Success!" in result.stdout or "Content ID" in result.stdout:
                return True
            if result.returncode == 0:
                return True
            print("[PUBLISH] Image post failed, falling back to text post.")
        else:
            print("[PUBLISH] post-image.mjs not found, falling back to text.")

    # Текстовый пост через навык
    cmd = ["node", script_path, "--text", text]
    print(f"[PUBLISH] Trying text post: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=skill_dir, env=env,
                           capture_output=True, text=True, timeout=60)
    print("[PUBLISH] STDOUT:", result.stdout)
    if result.stderr:
        print("[PUBLISH] STDERR:", result.stderr)
    if "Success!" in result.stdout or "Content ID" in result.stdout:
        return True
    if result.returncode == 0:
        return True

    # Если навык не справился – прямой API
    print("[PUBLISH] Skill failed, using direct API fallback.")
    return publish_direct_api(text)


def publish_direct_api(text):
    """Прямой вызов Binance Square API (только текст)."""
    url = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
    api_key = os.getenv("SQUARE_API")
    if api_key:
        api_key = api_key.strip()
    headers = {
        "X-Square-OpenAPI-Key": api_key,
        "clienttype": "binanceSkill",
        "Content-Type": "application/json"
    }
    payload = {"bodyTextOnly": text}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        print("[DIRECT API] STATUS:", r.status_code)
        print("[DIRECT API] RESPONSE:", r.text)
        if r.status_code == 200 and r.json().get("success", False):
            return True
        return False
    except Exception as e:
        print(f"[DIRECT API] ERROR: {e}")
        return False
