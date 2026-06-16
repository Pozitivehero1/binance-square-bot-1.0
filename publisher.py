import subprocess
import os
import tempfile
import glob

def publish(text):
    """
    Публикует пост через официальный навык Binance Square.
    Ищет скрипты в стандартных местах установки.
    """
    # Сохраняем текст во временный файл
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(text)
        text_file = f.name

    try:
        # Возможные пути к скриптам навыка
        possible_paths = [
            os.path.expanduser("~/.agents/skills/square-post/scripts/post-text.mjs"),
            os.path.expanduser("~/.skills/skills/square-post/scripts/post-text.mjs"),
            "./node_modules/@binance/square-post/scripts/post-text.mjs",
            "./skills/binance/square-post/scripts/post-text.mjs",
        ]
        
        script_path = None
        skill_dir = None
        
        for path in possible_paths:
            if os.path.exists(path):
                script_path = path
                skill_dir = os.path.dirname(os.path.dirname(path))  # папка навыка
                break
        
        if not script_path:
            print("[ERROR] Square Post skill not found. Tried:")
            for path in possible_paths:
                print(f"  - {path}")
            return False

        # Получаем API-ключ
        api_key = os.getenv("SQUARE_API") or os.getenv("BINANCE_SQUARE_OPENAPI_KEY")
        if not api_key:
            print("[ERROR] SQUARE_API environment variable not set")
            return False

        # Запускаем скрипт с ключом в окружении
        env = os.environ.copy()
        env["BINANCE_SQUARE_OPENAPI_KEY"] = api_key

        cmd = ["node", script_path, "--text", text_file]
        print(f"[PUBLISH] Running: {' '.join(cmd)}")
        print(f"[PUBLISH] Skill dir: {skill_dir}")

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
        print("[PUBLISH] RETURN CODE:", result.returncode)

        # Проверяем успешность
        if "Success!" in result.stdout or "Content ID" in result.stdout:
            return True
        elif result.returncode == 0:
            return True
        else:
            return False

    except Exception as e:
        print(f"[PUBLISH] ERROR: {e}")
        return False
    finally:
        if os.path.exists(text_file):
            os.remove(text_file)
