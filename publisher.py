import subprocess
import os
import tempfile

def publish(text, image_path=None):
    """
    Публикует пост через официальный навык Binance Square.
    Использует скрипт post-image.mjs из папки навыка.
    """
    # Ищем папку навыка
    skill_dir = None
    possible_dirs = [
        os.path.expanduser("~/.agents/skills/square-post"),
        os.path.expanduser("~/.skills/skills/square-post"),
        "./node_modules/@binance/square-post",
        "./skills/binance/square-post",
    ]
    for d in possible_dirs:
        if os.path.exists(os.path.join(d, "scripts", "post-image.mjs")):
            skill_dir = d
            break

    if not skill_dir:
        print("[PUBLISH] Skill not found.")
        return False

    # Получаем API-ключ
    api_key = os.getenv("SQUARE_API") or os.getenv("BINANCE_SQUARE_OPENAPI_KEY")
    if api_key:
        api_key = api_key.strip()
    else:
        print("[PUBLISH] No API key.")
        return False

    env = os.environ.copy()
    env["BINANCE_SQUARE_OPENAPI_KEY"] = api_key

    # Если есть изображение — используем post-image.mjs
    if image_path and os.path.exists(image_path):
        script = os.path.join(skill_dir, "scripts", "post-image.mjs")
        cmd = ["node", script, "--text", text, "--images", image_path]
        print(f"[PUBLISH] Running: {' '.join(cmd)}")
    else:
        # Только текст
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

        # Успех, если в выводе есть Success! или Content ID
        if "Success!" in result.stdout or "Content ID" in result.stdout:
            return True
        return result.returncode == 0

    except Exception as e:
        print(f"[PUBLISH] ERROR: {e}")
        return False
