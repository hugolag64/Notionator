from __future__ import annotations
import os
import re
import webbrowser
import customtkinter as ctk
import tkinter as tk
from typing import List, Dict, Optional, Tuple

# --- MarkdownText (optionnel) ---
try:
    from ui.markdown_text import MarkdownText
except Exception:
    MarkdownText = None  # fallback CTkTextbox

BG_OVERLAY = "black"
CARD_BG    = "#FFFFFF"
CARD_SHDW  = "#E8EAED"
TITLE_CLR  = "#0B1320"
TEXT_CLR   = "#1E1F22"
SUB_CLR    = "#6B7280"

MAX_SOURCES = 3  # ⇦ limite stricte d’affichage


class AIAnswerDialog(ctk.CTkToplevel):
    """
    Modal Apple-like.
    - show(...): bloquante (typewriter interne)
    - open(...): non-bloquante (stream via .append)
    Markdown si MarkdownText dispo.
    Réponse affichée **entière sans scroll**.
    Seules les **sources** sont scrollables.
    """

    # === API statique ===
    @staticmethod
    def show(parent, title: str, content: str,
             width: int = 920, height: int = 640,
             typing_speed_ms: int = 10,
             sources: Optional[List[Dict]] = None):
        dlg = AIAnswerDialog(parent, title, content, width, height, typing_speed_ms, sources or [])
        parent.wait_window(dlg)

    @staticmethod
    def open(parent, title: str,
             width: int = 920, height: int = 640,
             typing: bool = True,
             sources: Optional[List[Dict]] = None):
        dlg = AIAnswerDialog(parent, title, content="", width=width, height=height,
                             typing_speed_ms=10, sources=(sources or []))
        dlg._external_typing = bool(typing)
        return dlg

    # === Init ===
    def __init__(self, parent: tk.Tk, title: str, content: str,
                 width: int, height: int, typing_speed_ms: int,
                 sources: List[Dict]):
        super().__init__(parent)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        self._parent = parent
        self._desired_w = width
        self._desired_h = height
        self._external_typing = False
        self._stabilize_left = 6

        # --- robustesse asynchrone ---
        self._disposed = False
        self.bind("<Destroy>", lambda e: setattr(self, "_disposed", True), add="+")

        # Séparer “Sources : …” du corps de réponse (si présent)
        content_clean, embedded_sources = self._split_answer_and_sources(content)
        # préférences issues de la ligne “Sources : …” (priorisées)
        self._preferred_from_answer = embedded_sources or []

        # Overlay plein écran
        self._overlay = ctk.CTkToplevel(parent)
        self._overlay.overrideredirect(True)
        self._overlay.attributes("-alpha", 0.0)
        self._overlay.configure(fg_color=BG_OVERLAY)
        try:
            self._overlay.attributes("-topmost", True)
        except Exception:
            pass
        self._overlay.lift()
        self._overlay.bind("<Button-1>", lambda *_: self.close())
        self._overlay.bind("<Escape>", lambda *_: self.close())

        # Carte + ombre
        outer = ctk.CTkFrame(self, fg_color=CARD_SHDW, corner_radius=24)
        outer.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.body = ctk.CTkFrame(outer, fg_color=CARD_BG, corner_radius=18)
        self.body.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        # Layout global
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)
        # lignes body: 0 header | 1 sous-titre | 2 loader | 3 réponse | 4 sources (scrollable)
        self.body.grid_rowconfigure(3, weight=0)  # réponse fixe (sans scroll)
        self.body.grid_rowconfigure(4, weight=1)  # sources prennent le reste et scrollent
        self.body.grid_columnconfigure(0, weight=1)

        # Header
        header = ctk.CTkFrame(self.body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=title, font=("Helvetica", 22, "bold"),
                     text_color=TITLE_CLR).grid(row=0, column=0, sticky="w")
        btns = ctk.CTkFrame(header, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(btns, text="Copier", width=96, height=36,
                      command=self._copy_all).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="Fermer", width=96, height=36,
                      command=self.close).pack(side="left")

        # Sous-titre
        ctk.CTkLabel(self.body, text="Réponse générée à partir de tes sources locales",
                     font=("Helvetica", 12), text_color=SUB_CLR)\
            .grid(row=1, column=0, sticky="w", padx=20, pady=(0, 4))

        # Loader bandeau (unique)
        self.loader_frame = ctk.CTkFrame(self.body, fg_color="#F4F6FF", corner_radius=10)
        self.loader_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 6))
        self.loader_frame.grid_columnconfigure(0, weight=1)
        self.loader_label = ctk.CTkLabel(self.loader_frame, text="",
                                         text_color="#1E40AF", font=("Helvetica", 13, "bold"))
        self.loader_label.grid(row=0, column=0, sticky="w", padx=12, pady=8)
        self._loader_running = False
        self._loader_step = 0
        self._loader_base = "Je réfléchis"

        # Zone de réponse (sans scroll)
        if MarkdownText is not None:
            self.text = MarkdownText(self.body, corner_radius=12,
                                     fg_color="#FBFBFD", text_color=TEXT_CLR)
            if content_clean:
                self.text.set_markdown(content_clean)
        else:
            self.text = ctk.CTkTextbox(self.body, wrap="word", corner_radius=12,
                                       fg_color="#FBFBFD", text_color=TEXT_CLR, font=("Helvetica", 14))
            if content_clean:
                self.text.insert("end", content_clean)
        self.text.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 10))
        if hasattr(self.text, "bind"):
            self.text.bind("<Control-a>", lambda e: (self.text.tag_add("sel", "1.0", "end-1c"), "break"))

        # Bloc Sources scrollable
        self.sources_frame = ctk.CTkScrollableFrame(self.body, fg_color="transparent")
        self.sources_frame.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 12))
        self._render_sources([])

        # Raccourcis
        self.bind("<Escape>", lambda *_: self.close())
        self.bind("<Control-c>", lambda *_: self._copy_all())

        # Typewriter
        self._typing_speed = max(1, int(typing_speed_ms))
        self._tokens = (content_clean or "").split()
        self._i = 0

        # Placement & animation
        self._bind_sync()
        try:
            self.attributes("-alpha", 0.0)
        except Exception:
            pass

        self.deiconify()
        self._place_now()
        self.lift()
        self.after(15, self._stabilize_place)
        self.after(10, self._animate_in)
        if self._tokens:
            self.after(140, self._type_next)

        # premier autosize
        self.after(50, self._autosize_text_height)

        # Si on avait des sources intégrées à la réponse → on les affiche
        if embedded_sources:
            self.set_sources(embedded_sources)

    # ---------- vie / destruction ----------
    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists()) and not self._disposed
        except Exception:
            return False

    # ---------- Helpers : split "Sources:" ----------
    _re_sources_split = re.compile(r"(?is)\bSources?\s*:\s*(.*)$")

    def _split_answer_and_sources(self, content: str) -> Tuple[str, List[Dict]]:
        """Retourne (corps_sans_sources, sources_list) en détectant une ligne 'Sources : …' en fin de contenu."""
        if not content:
            return "", []
        m = self._re_sources_split.search(content)
        if not m:
            return content, []
        body = content[:m.start()].rstrip()
        tail = m.group(1).strip()
        return body, self._parse_sources_tail(tail)

    def _parse_sources_tail(self, tail: str) -> List[Dict]:
        """Parse 'ITEM 154 – p.8, XYZ – p.35' → [{'title':..., 'page':8}, ...]"""
        if not tail:
            return []
        possible: List[Dict] = []
        parts = re.split(r"[,\n\r]+", tail)
        for p in parts:
            p = p.strip("-• \t")
            if not p:
                continue
            mm = re.search(r"(.+?)\s*[–-]\s*p\.?\s*(\d+)", p)
            if mm:
                possible.append({"title": mm.group(1).strip(), "page": int(mm.group(2))})
            else:
                possible.append({"title": p.strip()})
        return possible

    # ---------- Dédoublonnage & choix représentatif ----------
    def _normalize_title(self, t: str) -> str:
        return re.sub(r"\s+", " ", (t or "").strip().lower())

    def _choose_representative(self, items: List[Dict], preferred_pages: set[int]) -> Dict:
        if not items:
            return {}
        if preferred_pages:
            for it in items:
                try:
                    if int(it.get("page", -1)) in preferred_pages:
                        return it
                except Exception:
                    pass
        pages = [int(it.get("page", 0)) for it in items if str(it.get("page", "")).isdigit()]
        if pages:
            pages.sort()
            median = pages[len(pages)//2]
            items_sorted = sorted(items, key=lambda i: abs(int(i.get("page", median) or median) - median))
            return items_sorted[0]
        return items[0]

    def _dedupe_and_limit(self, srcs: List[Dict], limit: int = MAX_SOURCES,
                          preferred: Optional[List[Dict]] = None) -> List[Dict]:
        preferred = preferred or []
        pref_map: Dict[str, set[int]] = {}
        for p in preferred:
            key = self._normalize_title(p.get("title", ""))
            try:
                pg = int(p.get("page")) if str(p.get("page", "")).isdigit() else None
                if key and pg is not None:
                    pref_map.setdefault(key, set()).add(pg)
            except Exception:
                continue

        groups: Dict[str, List[Dict]] = {}
        for s in srcs or []:
            key = self._normalize_title(s.get("title", ""))
            groups.setdefault(key, []).append(s)

        chosen: List[Dict] = []
        for key, items in groups.items():
            chosen.append(self._choose_representative(items, pref_map.get(key, set())))

        return chosen[:limit]

    # ---------- Rendu sources ----------
    def _render_sources(self, sources: List[Dict]):
        if not self._alive():
            return
        frame = getattr(self, "sources_frame", None)
        if frame is None or not frame.winfo_exists():
            return

        # purge défensive
        for w in list(frame.winfo_children()):
            try:
                w.destroy()
            except tk.TclError:
                pass

        if not sources:
            return

        try:
            title = ctk.CTkLabel(frame, text="Sources",
                                 font=("Helvetica", 12, "bold"), text_color=SUB_CLR)
            title.pack(anchor="w", pady=(2, 6))

            for i, s in enumerate(sources, start=1):
                label = f"{i}. {s.get('title','Document')} — p.{s.get('page','?')}"
                btn = ctk.CTkButton(
                    frame, text=label, height=34, corner_radius=10,
                    fg_color="#EEF2FF", hover_color="#E0E7FF", text_color="#1E40AF",
                    command=lambda src=s: self._open_source(src)
                )
                btn.pack(fill="x", pady=4)
        except tk.TclError:
            # La fenêtre peut être détruite pendant le rendu
            return

    def set_sources(self, sources: List[Dict]):
        """Nettoie le texte (supprime 'Sources : …'), dédoublonne/limite et rend."""
        if not self._alive():
            return
        # 1) s’assurer que la zone de texte n’affiche pas 'Sources : …'
        self.strip_sources_from_buffer()
        # 2) Dédoublonnage + limite
        cleaned = self._dedupe_and_limit(sources or [], MAX_SOURCES, self._preferred_from_answer)
        try:
            self._render_sources(cleaned)
        except tk.TclError:
            pass

    # ---------- Nettoyage de la zone texte après stream ----------
    def strip_sources_from_buffer(self):
        """Supprime la partie 'Sources : …' si elle est déjà affichée (cas streaming)."""
        if not self._alive():
            return
        try:
            full = self.text.get("1.0", "end-1c")
        except Exception:
            return
        body, found = self._split_answer_and_sources(full)
        if found:
            self._preferred_from_answer = found
            try:
                self.text.delete("1.0", "end")
                if hasattr(self.text, "set_markdown"):
                    self.text.set_markdown(body)
                else:
                    self.text.insert("end", body)
                if hasattr(self.text, "reparse_from_buffer"):
                    self.text.reparse_from_buffer()
                self._autosize_text_height()
            except tk.TclError:
                pass

    # ---------- Ouverture source ----------
    def _open_source(self, src: Dict):
        import pathlib, subprocess, shutil, sys, urllib.parse

        page = int(src.get("page", 1)) if str(src.get("page", "1")).isdigit() else 1
        path = src.get("path")
        url  = src.get("url")

        # 1) URL (Drive/web)
        if url:
            parsed = urllib.parse.urlparse(url)
            base = urllib.parse.urlunparse(parsed._replace(fragment=parsed.fragment))
            glue = "#" if not parsed.fragment else "&"
            webbrowser.open_new(f"{base}{glue}page={page}")
            return

        # 2) Fichier local -> navigateur file:// + #page=
        if path and os.path.exists(path):
            try:
                file_uri = pathlib.Path(path).resolve().as_uri()
                webbrowser.open_new(f"{file_uri}#page={page}")
                return
            except Exception:
                pass

            # 3) Fallback viewers
            if sys.platform.startswith("win"):
                candidates = [
                    r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
                    r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
                    r"C:\Program Files\Adobe\Acrobat DC\Acrobat\Acrobat.exe",
                    r"C:\Program Files (x86)\Adobe\Acrobat DC\Acrobat\Acrobat.exe",
                ]
                exe = next((p for p in candidates if os.path.exists(p)), None)
                if exe and "SumatraPDF" in exe:
                    try:
                        subprocess.Popen([exe, "-page", str(page), path], shell=False)
                        return
                    except Exception:
                        pass
                elif exe and "Acrobat" in exe:
                    try:
                        subprocess.Popen([exe, path], shell=False)
                        return
                    except Exception:
                        pass

                os.startfile(path)
                return

            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    opener = shutil.which("xdg-open") or shutil.which("gio")
                    if opener:
                        subprocess.Popen([opener, path])
                return
            except Exception:
                pass

        self.append(f"\n[Impossible d’ouvrir la source : {src}]")

    # ---------- Placement / animation ----------
    def _bind_sync(self):
        self._parent.bind("<Configure>", self._on_parent_configure, add="+")
        self.bind("<Map>", lambda *_: self._place_now(), add="+")
        self._overlay.bind("<Map>", lambda *_: self._place_now(), add="+")

    def _on_parent_configure(self, _evt=None):
        if getattr(self, "_place_scheduled", False):
            return
        self._place_scheduled = True
        self.after(16, self._place_now)

    def _get_screen_rect(self):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        return 0, 0, sw, sh

    def _place_now(self):
        if not self._alive():
            return
        self._place_scheduled = False
        px, py, pw, ph = self._get_screen_rect()
        try:
            self._overlay.geometry(f"{pw}x{ph}+{px}+{py}")
        except Exception:
            pass

        # large & haut (fenêtre quasi plein écran)
        self._final_w = min(self._desired_w, int(pw * 0.92))
        self._final_h = min(self._desired_h, int(ph * 0.88))
        x = max(0, px + (pw - self._final_w) // 2)
        y = max(0, py + (ph - self._final_h) // 2)
        self._final_x, self._final_y = x, y

        if not getattr(self, "_animating", False):
            try:
                self.geometry(f"{self._final_w}x{self._final_h}+{self._final_x}+{self._final_y}")
            except Exception:
                pass

        try:
            self._overlay.lift()
            self.lift()
        except Exception:
            pass

    def _stabilize_place(self):
        if self._stabilize_left <= 0 or not self._alive():
            return
        self._stabilize_left -= 1
        self._place_now()
        self.after(30, self._stabilize_place)
        # re-autosize (utile au tout début)
        self._autosize_text_height()

    def _animate_in(self, duration_ms: int = 180, steps: int = 12,
                    start_scale: float = 0.92, overlay_alpha: float = 0.28, card_alpha: float = 0.98):
        if not self._alive():
            return
        self._animating = True
        self._place_now()

        def ease(t: float) -> float:
            return 1 - (1 - t) ** 3

        cx = self._final_x + self._final_w / 2
        cy = self._final_y + self._final_h / 2

        for i in range(steps + 1):
            if not self._alive():
                return
            t = i / steps
            e = ease(t)
            scale = start_scale + (1.0 - start_scale) * e
            w = int(self._final_w * scale)
            h = int(self._final_h * scale)
            x = int(cx - w / 2)
            y = int(cy - h / 2)

            try:
                self.geometry(f"{w}x{h}+{x}+{y}")
                self.attributes("-alpha", card_alpha * e)
                self._overlay.attributes("-alpha", overlay_alpha * e)
            except Exception:
                pass

            self.update_idletasks()
            self.after(int(duration_ms / max(1, steps)))

        try:
            self.geometry(f"{self._final_w}x{self._final_h}+{self._final_x}+{self._final_y}")
            self.attributes("-alpha", card_alpha)
            self._overlay.attributes("-alpha", overlay_alpha)
        except Exception:
            pass
        self._animating = False

    # ---------- Typewriter ----------
    def _type_next(self):
        if not self._alive():
            return
        if self._external_typing:
            return
        if self._i >= len(self._tokens):
            if MarkdownText is not None:
                try:
                    self.text.reparse_from_buffer()
                except Exception:
                    pass
            self._autosize_text_height()
            return
        burst = 4
        end = min(self._i + burst, len(self._tokens))
        chunk = " ".join(self._tokens[self._i:end]) + (" " if end < len(self._tokens) else "")
        self.append(chunk)
        self._i = end
        self._autosize_text_height()
        self.after(self._typing_speed, self._type_next)

    # ---------- Loader ----------
    def start_loader(self, message: str = "Je réfléchis"):
        if not self._alive():
            return
        self._loader_base = message
        self._loader_running = True
        self._loader_step = 0
        try:
            self.loader_frame.grid()
            self._tick_loader()
        except tk.TclError:
            pass

    def _tick_loader(self):
        if not getattr(self, "_loader_running", False) or not self._alive():
            return
        try:
            dots = "." * (1 + (self._loader_step % 3))
            self.loader_label.configure(text=f"{self._loader_base}{dots}")
            self._loader_step += 1
            self.after(300, self._tick_loader)
        except tk.TclError:
            pass

    def stop_loader(self):
        self._loader_running = False
        if not self._alive():
            return
        try:
            self.loader_frame.grid_remove()
        except tk.TclError:
            pass

    # ---------- Autosize zone de texte (pas de scroll) ----------
    def _autosize_text_height(self):
        """Ajuste la hauteur pour afficher tout le contenu, min 300px, max ~85% de la fenêtre."""
        if not self._alive():
            return
        try:
            end_index = self.text.index("end-1c")
            total_lines = int(end_index.split(".")[0])
            dli = self.text.dlineinfo("1.0")
            line_h = dli[3] if dli else 18
            target_px = max(300, min(total_lines * line_h + 14, int(self._final_h * 0.85)))
            self.text.configure(height=target_px)
            self.update_idletasks()
        except Exception:
            pass

    # ---------- Utils ----------
    def _copy_all(self):
        if not self._alive():
            return
        try:
            txt = self.text.get("1.0", "end").strip()
            if txt:
                self.clipboard_clear()
                self.clipboard_append(txt)
        except Exception:
            pass

    def append(self, text: str):
        if not self._alive() or not text:
            return
        try:
            if MarkdownText is not None and hasattr(self.text, "append_plain"):
                self.text.append_plain(text)
            else:
                self.text.insert("end", text)
                self.text.see("end")
        except tk.TclError:
            pass

    def close(self):
        # Marque comme disposé pour couper tous les callbacks .after en douceur
        self._disposed = True
        try:
            self._overlay.destroy()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
