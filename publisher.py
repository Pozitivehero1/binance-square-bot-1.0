import subprocess
import os
import tempfile

def publish(text, image_path=None):
    """
    Публикует пост через официальный навык Binance Square.
    Если передан image_path – публикует с изображением.
    """
    # Сохраняем текст во временный файл, чтобы избежать проблем с экранированием
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(text)
        text_file = f.name

    try:
        if image_path and os.path.exists(image_path):
            # Пост с изображением
            cmd = [
                "npx", "skills", "run", "square-post",
                "--text", text_file,
                "--images", image_path
            ]
        else:
            # Текстовый пост
            cmd = [
                "npx", "skills", "run", "square-post",
                "--text", text_file
            ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        print("RETURN CODE:", result.returncode)

        # Проверяем успешность по stdout (навык выводит ID поста при успехе)
        if "Post created" in result.stdout or "Content ID" in result.stdout:
            return True
        else:
            return False

    finally:
        # Удаляем временный файл
        if os.path.exists(text_file):
            os.remove(text_file)
