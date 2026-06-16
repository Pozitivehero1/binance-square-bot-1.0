import subprocess
import os
import tempfile
import shutil

def publish(text):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(text)
        text_file = f.name

    try:
        # Список возможных команд
        commands = []

        # 1. Локальный скрипт (если навык установлен в node_modules)
        local_script = "./node_modules/@binance/square-post/scripts/post-text.mjs"
        if os.path.exists(local_script):
            commands.append(["node", local_script, "--text", text_file])

        # 2. Через npx с явным указанием пакета
        commands.append(["npx", "@binance/square-post", "post-text", "--text", text_file])

        # 3. Через npx skills (если установлен глобально)
        commands.append(["npx", "skills", "run", "square-post", "--text", text_file])

        # 4. Прямой вызов через node_modules/.bin (если есть)
        bin_script = "./node_modules/.bin/square-post"
        if os.path.exists(bin_script):
            commands.append([bin_script, "post-text", "--text", text_file])

        for cmd in commands:
            try:
                print(f"Trying: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    shell=False
                )
                print("STDOUT:", result.stdout)
                print("STDERR:", result.stderr)
                print("RETURN CODE:", result.returncode)

                # Успех, если в выводе есть Content ID или Post created
                if "Content ID" in result.stdout or "Post created" in result.stdout:
                    return True
                # Если код 0 и нет ошибок — тоже считаем успехом
                if result.returncode == 0 and "error" not in result.stderr.lower():
                    return True
            except Exception as e:
                print(f"Command failed: {e}")
                continue

        # Если ни одна команда не сработала
        print("All commands failed.")
        return False

    except Exception as e:
        print("PUBLISH ERROR:", e)
        return False
    finally:
        if os.path.exists(text_file):
            os.remove(text_file)
