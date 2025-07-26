import json
from config import NOTES_FILE

def load_notes():
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_notes(notes):
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=4)
