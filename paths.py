"""
Где BookReader хранит свои данные — без абсолютных путей и без записи в папку программы.

- data_dir()   — данные пользователя: настройки сервера, журналы, скачанный словарь.
                 Windows: %LOCALAPPDATA%\\BookReader, macOS: ~/Library/Application Support/BookReader,
                 Linux: $XDG_DATA_HOME/bookreader (обычно ~/.local/share/bookreader).
                 Переопределяется переменной окружения BOOKREADER_HOME.
- writable_dir(preferred, fallback) — папка рядом с программой, если туда можно писать
  (переносная установка), иначе — такая же папка внутри data_dir(). Так программа работает
  и из «Program Files», куда без прав администратора писать нельзя.
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))   # папка программы (server.py и т. д.)


def data_dir():
    env = os.environ.get("BOOKREADER_HOME")
    if env:
        path = os.path.abspath(os.path.expanduser(env))
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        path = os.path.join(base, "BookReader")
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "BookReader")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        path = os.path.join(base, "bookreader")
    os.makedirs(path, exist_ok=True)
    return path


def logs_dir():
    path = os.path.join(data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def config_path():
    return os.path.join(data_dir(), "server.json")


def can_write(path):
    """Можно ли создать/записать файл в папке path (папку создаёт, если её нет)."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write-test")
        with open(probe, "w", encoding="ascii") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def writable_dir(preferred, fallback_name):
    """preferred, если туда можно писать, иначе data_dir()/fallback_name."""
    if can_write(preferred):
        return preferred
    path = os.path.join(data_dir(), fallback_name)
    os.makedirs(path, exist_ok=True)
    return path
