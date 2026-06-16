import subprocess
import os
import tempfile

def publish(text):
    """
    Публикует пост через официальный навык Binance Square.
    Возвращает True при успехе, иначе False.
    """
    # Сохраняем текст во временный файл
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(text)
        text_file = f.name

    try:
        # Пробуем запустить скрипт напрямую (если навык установлен локально)
        script_path = "./node_modules/@binance/square-post/scripts/post-text.mjs"
        if os.path.exists(script_path):
            cmd = ["node", script_path, "--text", text_file]
        else:
            # Альтернатива: используем npx skills execute
            cmd = ["npx", "skills", "execute", "square-post", "--text", text_file]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        print("RETURN CODE:", result.returncode)

        # Успех, если в выводе есть "Content ID" или "Post created" или код 0
        if "Content ID" in result.stdout or "Post created" in result.stdout:
            return True
        # Если код возврата 0, но мы не нашли ключевых фраз — тоже считаем успехом
        if result.returncode == 0:
            return True
        return False

    except Exception as e:
        print("PUBLISH ERROR:", e)
        return False
    finally:
        # Удаляем временный файл
        if os.path.exists(text_file):
            os.remove(text_file)
