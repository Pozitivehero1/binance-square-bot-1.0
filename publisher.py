import subprocess
import os
import tempfile
import requests

def publish(text, image_path=None):
    """
    Публикует пост. Если передан image_path, пытается опубликовать с изображением через навык.
    Если навык не найден или не работает, использует прямой API (без изображения).
    """
    # Ищем скрипты навыка
    script_path = None
    skill_dir = None
    possible_paths = [
        os.path.expanduser("~/.agents/skills/square-post/scripts/post-text.mjs"),
        os.path.expanduser("~/.skills/skills/square-post/scripts/post-text.mjs"),
        "./node_modules/@binance/square-post/scripts/post-text.mjs",
        "./skills/binance/square-post/scripts/post-text.mjs",
    ]
    for path in possible_paths:
        if os.path.exists(path):
            script_path = path
            skill_dir = os.path.dirname(os.path.dirname(path))
            break

    # Если навык не найден – используем прямой API (без изображения)
    if not script_path:
        print("[PUBLISH] Skill not found, using direct API (text only).")
        return publish_direct_api(text)

    # Получаем и очищаем API-ключ
    api_key = os.getenv("SQUARE_API") or os.getenv("BINANCE_SQUARE_OPENAPI_KEY")
    if api_key:
        api_key = api_key.strip()
    else:
        print("[PUBLISH] No API key, using direct API.")
        return publish_direct_api(text)

    env = os.environ.copy()
    env["BINANCE_SQUARE_OPENAPI_KEY"] = api_key

    # Если есть изображение – используем post-image.mjs
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
            # Если не вышло – пробуем текстовый пост
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
