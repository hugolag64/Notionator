# reset_index.py
import os
import json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

FILES = {
    "pdf_index.faiss": None,        # → supprimé
    "pdf_metadata.json": [],        # → liste vide
    "pdf_mapping.json": {},         # → dict vide
    "pdf_registry.json": {},        # → dict vide
    "pdf_autoscan_state.json": {},  # → dict vide
}

def reset():
    os.makedirs(DATA_DIR, exist_ok=True)
    for name, default in FILES.items():
        path = os.path.join(DATA_DIR, name)
        if default is None:
            # Fichier binaire FAISS → on supprime
            if os.path.exists(path):
                os.remove(path)
                print(f"[OK] Supprimé : {name}")
            else:
                print(f"[SKIP] Absent déjà : {name}")
        else:
            # JSON → on écrit la structure vide
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)
            print(f"[OK] Réinitialisé : {name}")

if __name__ == "__main__":
    reset()
    print("\n✔ Réinitialisation complète terminée. "
          "Relance l'app pour reconstruire l'index FAISS.")
