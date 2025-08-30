# services/textfmt.py
from __future__ import annotations
import re

_WORD = r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+"
_SENT_SPLIT = re.compile(r"(?<=[\.\?\!;:])\s+")
_NUM_TOKEN  = re.compile(r"(?:(?<=^)|(?<=\s))(\d+)[\.\)]\s+")
_DASH_GAP   = re.compile(r"\s[–—-]\s")   # espace + tiret/emdash + espace

def _normalize(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").split())

def _numbered_to_bullets(t: str) -> str:
    """Convertit '1. aaa 2. bbb 3. ccc' → lignes '- aaa\\n- bbb\\n- ccc'."""
    matches = list(_NUM_TOKEN.finditer(t))
    if len(matches) < 3:
        return t  # on évite de sur-split si juste '1.' et '2.' sporadiques

    head = t[:matches[0].start()].strip()
    items: list[str] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i+1].start() if i+1 < len(matches) else len(t)
        chunk = t[start:end].strip(" ;,.-•")
        if not chunk:
            continue
        # si le chunk contient des sous-éléments séparés par " - " → on les éclate aussi
        sub = _DASH_GAP.split(chunk)
        for s in sub:
            s = s.strip(" ;,.-•")
            if s:
                items.append(s)

    if not items:
        return t

    out = (head + "\n" if head else "") + "\n".join(f"- {it}" for it in items)
    return out.strip()

def auto_markdownify(text: str, max_paragraph_chars: int = 140) -> str:
    """
    Heuristiques légères pour rendre lisible :
    - normalise blancs
    - '1. ... 2. ...' → puces
    - ' - ' / ' — ' / ' – ' / ' • ' → ruptures de ligne + '- '
    - si c'est encore un bloc unique trop long → coupe par phrases
    """
    t = _normalize(text)

    # 1) listes numérotées → puces
    t1 = _numbered_to_bullets(t)

    # 2) séquences ' - ' / ' — ' / ' – ' / ' • ' → puces
    t2 = (
        t1.replace(" • ", "\n- ")
          .replace(" — ", "\n- ")
          .replace(" – ", "\n- ")
          .replace(" - ", "\n- ")
    )

    # 3) si toujours un seul paragraphe long → coupe par phrases
    if "\n" not in t2 and len(t2) > max_paragraph_chars:
        parts = _SENT_SPLIT.split(t2)
        t2 = "\n".join(p.strip() for p in parts if p.strip())

    # 4) nettoyage
    t2 = re.sub(r"\n{3,}", "\n\n", t2).strip()

    # garde au moins deux puces si on a transformé une liste
    if t2.count("\n- ") >= 1 or t2.startswith("- "):
        return t2
    return t2
