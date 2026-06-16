import subprocess
import os
import tempfile
import shutil

def publish(text):
    """
    Публикует пост через официальный навык Binance Square.
    Использует локальные скрипты из node_modules.
    """
    # Путь к скриптам навыка (после установки через npx skills add)
    skill_dir = "./node_modules/@binance/square-post"
    script_path = os.path.join(skill_dir, "scripts", "post-text.mjs")
    
    # Если навык не установлен локально, пробуем найти в другом месте
    if not os.path.exists(script_path):
        # Пробуем alternative path (если установлен вручную)
        alt_path = "./skills/binance/square-post/scripts/post-text.mjs"
        if os.path.exists(alt_path):
            script_path = alt_path
            skill_dir = "./skills/binance/square-post"
        else:
            print("[ERROR] Square Post skill not found. Run: npx skills add https://github.com/binance/binance-skills-hub")
            return False

    # Сохраняем текст во временный файл (чтобы избежать проблем с экранированием)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(text)
        text_file = f.name

    try:
        # Получаем API-ключ из переменной окружения
        api_key = os.getenv("SQUARE_API") or os.getenv("BINANCE_SQUARE_OPENAPI_KEY")
        
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
