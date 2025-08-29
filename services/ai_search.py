# services/ai_search.py
from __future__ import annotations
from typing import Iterable

AI_NO_QUERY_MSG = "⚠️ Entrez une question."
AI_ENGINE_MISSING = "🤖 Le moteur local n'est pas initialisé. (services.local_search introuvable)"
AI_NO_API_MSG = "🤖 Aucune fonction (ask/qa/answer/search) trouvée dans services.local_search."

def _load_engine():
    try:
        from services import local_search  # import paresseux pour éviter les cycles
        return local_search
    except Exception:
        return None

# -------------------------------
# Réponse "one-shot" (texte)
# -------------------------------
def ask(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return AI_NO_QUERY_MSG

    engine = _load_engine()
    if engine is None:
        return AI_ENGINE_MISSING

    # On essaie différentes API possibles côté moteur
    for fn_name in ("ask", "qa", "answer", "search"):
        fn = getattr(engine, fn_name, None)
        if callable(fn):
            try:
                res = fn(query)
                if isinstance(res, (list, tuple)):
                    res = "\n".join(map(str, res))
                return str(res) if res else "Aucune réponse trouvée."
            except Exception as e:
                return f"Erreur du moteur local : {e!r}"

    return AI_NO_API_MSG

# -------------------------------
# Réponse en streaming (chunks)
# -------------------------------
def stream(query: str) -> Iterable[str]:
    """
    Renvoie un générateur de morceaux de texte à afficher au fil de l'eau.
    - Si le moteur expose `stream`, on l'utilise directement.
    - Sinon, on fallback: on appelle `ask(query)` puis on 'stream' le texte par bursts.
    """
    query = (query or "").strip()
    if not query:
        yield AI_NO_QUERY_MSG
        return

    engine = _load_engine()
    if engine is None:
        yield AI_ENGINE_MISSING
        return

    # 1) Cas idéal: moteur avec streaming natif
    fn_stream = getattr(engine, "stream", None)
    if callable(fn_stream):
        try:
            for chunk in fn_stream(query):
                if chunk:
                    yield str(chunk)
        except Exception as e:
            yield f"\n[Erreur moteur stream: {e!r}]"
        return

    # 2) Fallback: one-shot → on découpe proprement pour l'UI
    text = ask(query)
    if not text:
        yield "Aucune réponse."
        return

    # Découpage 'word bursts' (plus naturel visuellement)
    words = text.split()
    i, n = 0, len(words)
    burst = 8 if n > 800 else 5  # bursts plus grands pour gros textes
    while i < n:
        j = min(i + burst, n)
        yield " ".join(words[i:j]) + (" " if j < n else "")
        i = j

# -------------------------------
# (Optionnel) Réponse + sources
# -------------------------------
def ask_with_sources(query: str) -> dict:
    """
    Si le moteur expose answer_with_sources(query) -> {answer, sources}, on le relaie.
    Sinon, renvoie {"answer": ask(query), "sources": []}.
    """
    engine = _load_engine()
    if engine is None:
        return {"answer": AI_ENGINE_MISSING, "sources": []}

    fn = getattr(engine, "answer_with_sources", None)
    if callable(fn):
        try:
            res = fn(query)
            # Assure la structure minimale
            if not isinstance(res, dict):
                return {"answer": str(res), "sources": []}
            res.setdefault("answer", "")
            res.setdefault("sources", [])
            return res
        except Exception as e:
            return {"answer": f"Erreur moteur (sources): {e!r}", "sources": []}

    # Fallback simple
    return {"answer": ask(query), "sources": []}
