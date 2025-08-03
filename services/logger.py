import logging
import os
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "log_notionator.log")

# Handler avec rotation quotidienne, sans limite sur le nombre de fichiers
file_handler = TimedRotatingFileHandler(
    LOG_FILE, when="midnight", interval=1, backupCount=0, encoding="utf-8", utc=True
)
file_handler.suffix = "%Y-%m-%d"
file_handler.setLevel(logging.INFO)  # <-- Niveau info et plus (pas debug !)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.WARNING)  # Console = juste warning+erreur

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[file_handler, stream_handler]
)

def get_logger(name: str):
    return logging.getLogger(name)

# Nettoyage des logs vieux de 72h
def cleanup_old_logs(log_dir=LOG_DIR, keep_hours=72):
    now = datetime.utcnow()
    for filename in os.listdir(log_dir):
        if not filename.startswith("log_notionator.log"):
            continue
        path = os.path.join(log_dir, filename)
        if not os.path.isfile(path):
            continue
        try:
            stat = os.stat(path)
            file_time = datetime.utcfromtimestamp(stat.st_mtime)
            if now - file_time > timedelta(hours=keep_hours):
                os.remove(path)
        except Exception:
            pass

cleanup_old_logs()
