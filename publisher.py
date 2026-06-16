import subprocess
import os
import tempfile
import shutil

def publish(text):
    """
    Публикует пост через официальный навык Binance Square.
    Сначала пытается через навык, при ошибке использует прямой API.
    """
    # Сохраняем текст во временный файл
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(text)
        text_file = f.name

    try:
        # Пытаемся найти скрипт навыка
        script_path = None
        skill_dir = None

        # Возможные пути
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

        if not script_path:
            print("[PUBLISH] Skill not found, using direct API fallback.")
            return publish_direct_api(text)

        # Получаем API-ключ
        api_key = os.getenv("SQUARE_API") or os.getenv("BINANCE_SQUARE_OPENAPI_KEY")
        if not api_key:
            print("[PUBLISH] No API key, using direct API fallback.")
            return publish_direct_api(text)

        # Запускаем скрипт с флагом --text-file (если поддерживается)
        env = os.environ.copy()
        env["BINANCE_SQUARE_OPENAPI_KEY"] = api_key

        # Пробуем с --text-file
        cmd = ["node", script_path, "--text-file", text_file]
        print(f"[PUBLISH] Trying: {' '.join(cmd)}")
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

        # Если успешно – возвращаем True
        if "Success!" in result.stdout or "Content ID" in result.stdout:
            return True
        elif result.returncode == 0:
            return True

        # Если не получилось, пробуем передать текст напрямую (экранируем)
        print("[PUBLISH] --text-file failed, trying direct text...")
        cmd2 = ["node", script_path, "--text", text]
        result2 = subprocess.run(
            cmd2,
            cwd=skill_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=60
        )
        if "Success!" in result2.stdout or "Content ID" in result2.stdout:
            return True
        elif result2.returncode == 0:
            return True

        # Если всё равно не вышло – fallback на прямой API
        print("[PUBLISH] Skill failed, using direct API fallback.")
        return publish_direct_api(text)

    except Exception as e:
        print(f"[PUBLISH] ERROR: {e}")
        return publish_direct_api(text)
    finally:
        if os.path.exists(text_file):
            os.remove(text_file)


def publish_direct_api(text):
    """Прямой вызов Binance Square API (проверенный метод)."""
    import requests
    url = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
    headers = {
        "X-Square-OpenAPI-Key": os.getenv("SQUARE_API"),
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
