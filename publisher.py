import subprocess
import os
import tempfile
import glob

def publish(text):
    # Сохраняем текст во временный файл
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(text)
        text_file = f.name

    try:
        # Ищем скрипт post-text.mjs в стандартных местах
        possible_roots = [
            os.path.expanduser("~"),
            ".",
        ]
        script_path = None
        skill_dir = None

        for root in possible_roots:
            # Ищем рекурсивно
            for dirpath, dirnames, filenames in os.walk(root):
                if "post-text.mjs" in filenames:
                    script_path = os.path.join(dirpath, "post-text.mjs")
                    skill_dir = dirpath
                    break
            if script_path:
                break

        if not script_path:
            print("[ERROR] Square Post skill not found.")
            return False

        print(f"[PUBLISH] Found skill at: {script_path}")

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
