# ui/markdown_text.py
from __future__ import annotations

import re
import webbrowser
import customtkinter as ctk
import tkinter as tk
import tkinter.font as tkfont
from tkinter import TclError


class MarkdownText(ctk.CTkTextbox):
    """
    CTkTextbox avec rendu Markdown minimal (Apple-like) :
    - **gras**, *italique*, `code`
    - Titres (#, ##, ###)
    - Listes (- , * )
    - Liens [texte](url) cliquables

    Notes robustesse :
    - reparse_from_buffer() est safe si le widget est détruit (no TclError)
    - schedule_reparse() annule/replace le after() précédent (debounce)
    """

    # --- Regex inline ---
    _re_link   = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    _re_bold   = re.compile(r"\*\*(.+?)\*\*")
    _re_italic = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
    _re_code   = re.compile(r"`([^`]+)`")

    def __init__(self, parent, **kwargs):
        super().__init__(parent, wrap="word", **kwargs)

        # tkinter.Text interne de CTkTextbox
        self._t: tk.Text = self._textbox  # type: ignore[attr-defined]

        # Gestion du reparse différé
        self._reparse_after_id: str | None = None
        self.bind("<Destroy>", self._on_destroy, add="+")

        # Style
        self.configure(state="disabled")
        self._link_count = 0
        self._fonts = {
            "normal": ("Helvetica", 14),
            "bold":   ("Helvetica", 14, "bold"),
            "italic": ("Helvetica", 14, "italic"),
            "h1":     ("Helvetica", 18, "bold"),
            "h2":     ("Helvetica", 16, "bold"),
            "h3":     ("Helvetica", 15, "bold"),
            "mono":   ("SF Mono", 13) if self._has_sf_mono() else ("Courier New", 13),
        }
        # Tags de style sur le Text interne
        self._t.tag_configure("bold",   font=self._fonts["bold"])
        self._t.tag_configure("italic", font=self._fonts["italic"])
        self._t.tag_configure("h1",     font=self._fonts["h1"])
        self._t.tag_configure("h2",     font=self._fonts["h2"])
        self._t.tag_configure("h3",     font=self._fonts["h3"])
        self._t.tag_configure("code",   font=self._fonts["mono"])
        self._t.tag_configure("bullet_indent", lmargin1=20, lmargin2=40)
        # Style des liens
        self._t.tag_configure("link", underline=True)
        try:
            self._t.tag_configure("link", foreground="#2563EB")  # bleu sobre
        except Exception:
            pass

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def set_markdown(self, md: str):
        """Efface et rend tout le markdown."""
        if not self._widget_alive():
            return
        try:
            self.configure(state="normal")
            self.delete("1.0", "end")
            self._link_count = 0
            self._render_markdown(md or "")
            self.configure(state="disabled")
        except TclError:
            # Widget détruit pendant l'opération → on ignore proprement
            return

    def append_plain(self, text: str):
        """Append brut (utile en streaming), sans parsing."""
        if not text or not self._widget_alive():
            return
        try:
            self.configure(state="normal")
            self.insert("end", text)
            self.see("end")
            self.configure(state="disabled")
        except TclError:
            return

    def schedule_reparse(self, delay_ms: int = 150):
        """Debounce : planifie un reparse en annulant le précédent si nécessaire."""
        if not self._widget_alive():
            return
        if self._reparse_after_id:
            try:
                self.after_cancel(self._reparse_after_id)
            except Exception:
                pass
        self._reparse_after_id = self.after(delay_ms, self.reparse_from_buffer)

    def reparse_from_buffer(self):
        """Reparse le contenu actuel (après un stream) pour appliquer le markdown."""
        # consomme l'after courant
        self._reparse_after_id = None

        if not self._widget_alive():
            return
        try:
            md = self.get("1.0", "end-1c")
        except TclError:
            # l'objet Tcl a été détruit entre-temps
            return
        self.set_markdown(md)

    # --------------------------------------------------------------------- #
    # Rendering
    # --------------------------------------------------------------------- #
    def _render_markdown(self, md: str):
        lines = (md.replace("\r\n", "\n").replace("\r", "\n")).split("\n")
        for raw in lines:
            if not self._widget_alive():
                return
            line = raw.rstrip()

            # Titres
            if line.startswith("### "):
                self._insert_styled(line[4:] + "\n", "h3")
                continue
            if line.startswith("## "):
                self._insert_styled(line[3:] + "\n", "h2")
                continue
            if line.startswith("# "):
                self._insert_styled(line[2:] + "\n", "h1")
                continue

            # Puces
            if line.lstrip().startswith(("- ", "* ")):
                content = line.lstrip()[2:]
                self._insert_bullet(content)
                continue

            # Paragraphe normal avec inline styles
            self._insert_inline(line + "\n")

    def _insert_bullet(self, text: str):
        if not self._widget_alive():
            return
        self._t.insert("end", "• ", ("bullet_indent",))
        self._insert_inline(text + "\n", extra_tags=("bullet_indent",))

    def _insert_styled(self, text: str, tag: str):
        if not self._widget_alive():
            return
        self._t.insert("end", text, (tag,))

    def _insert_inline(self, text: str, extra_tags: tuple[str, ...] = ()):
        """
        Insère une ligne en gérant liens / code / gras / italique.
        Ordre: liens -> code -> gras -> italique (évite les conflits).
        """
        if not self._widget_alive():
            return

        idx = 0
        while idx < len(text):
            # matches
            m = self._re_link.search(text, idx)
            m_code = self._re_code.search(text, idx)
            m_bold = self._re_bold.search(text, idx)
            m_it   = self._re_italic.search(text, idx)

            candidates = [x for x in (m, m_code, m_bold, m_it) if x]
            if not candidates:
                self._t.insert("end", text[idx:], extra_tags)
                break
            nxt = min(candidates, key=lambda mo: mo.start())

            if nxt.start() > idx:
                self._t.insert("end", text[idx:nxt.start()], extra_tags)

            if nxt is m:  # lien
                label, url = m.group(1), m.group(2)
                self._insert_link(label, url, extra_tags)
            elif nxt is m_code:
                self._t.insert("end", m_code.group(1), (*extra_tags, "code"))
            elif nxt is m_bold:
                self._t.insert("end", m_bold.group(1), (*extra_tags, "bold"))
            elif nxt is m_it:
                self._t.insert("end", m_it.group(1), (*extra_tags, "italic"))

            idx = nxt.end()

    def _insert_link(self, label: str, url: str, extra_tags: tuple[str, ...]):
        if not self._widget_alive():
            return
        self._link_count += 1
        tag = f"link_{self._link_count}"
        self._t.insert("end", label, (*extra_tags, "link", tag))
        self._t.tag_bind(tag, "<Button-1>", lambda _e, u=url: webbrowser.open_new(u))

    # --------------------------------------------------------------------- #
    # Helpers robustesse
    # --------------------------------------------------------------------- #
    def _on_destroy(self, *_):
        """Annule tout after en attente quand le widget disparaît."""
        if self._reparse_after_id:
            try:
                self.after_cancel(self._reparse_after_id)
            except Exception:
                pass
            self._reparse_after_id = None

    def _widget_alive(self) -> bool:
        """Vérifie que le widget et son Text interne existent encore côté Tcl."""
        try:
            if not self.winfo_exists():
                return False
            inner = getattr(self, "_textbox", None)
            if inner is None:
                return False
            # winfo_exists renvoie "1"/"0" parfois → cast en int/bool
            return bool(int(inner.winfo_exists()))
        except Exception:
            return False

    def _has_sf_mono(self) -> bool:
        try:
            return "SF Mono" in tkfont.families()
        except Exception:
            return False
