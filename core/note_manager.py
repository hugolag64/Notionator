from .database import load_notes, save_notes

def get_all_notes():
    return load_notes()

def add_note(title, content):
    notes = load_notes()
    notes.append({"title": title, "content": content})
    save_notes(notes)
