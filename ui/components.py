#ui/components.py
import customtkinter as ctk
from .styles import COLORS

# ---------- Dialog sélection d’UE (single) ----------
# ui/components_ue_dialog.py (ou dans ui/components.py)
import customtkinter as ctk

class UEDialogSingleSelect(ctk.CTkToplevel):
    """
    Affiche une liste d'UE lisible (noms) et renvoie les IDs sélectionnés.
    - ue_items: liste de tuples [(id, label), ...]. Si None, récupère via parent.notion_api.get_ue()
    - on_validate: callback(list[str]) optionnel
    """
    def __init__(self, parent, ue_items=None, on_validate=None, title="Associer une UE"):
        super().__init__(parent)
        self.transient(parent)
        self.grab_set()
        self.title(title)
        self.geometry("520x240")
        self._on_validate = on_validate
        self.result: list[str] | None = None

        # -- données
        if ue_items is None:
            ue_items = []
            try:
                pages = parent.notion_api.get_ue()
            except Exception:
                pages = []
            def _ue_name(p):
                t = p.get("properties", {}).get("UE", {}).get("title", [])
                return t[0]["text"]["content"] if t and t[0].get("text") else "Sans titre"
            for p in pages:
                ue_items.append((p["id"], _ue_name(p)))

        # normalisation -> [(id,label)]
        norm = []
        for it in ue_items:
            if isinstance(it, tuple) and len(it) == 2:
                norm.append((it[0], it[1]))
            elif isinstance(it, dict) and "id" in it and "label" in it:
                norm.append((it["id"], it["label"]))
        if not norm:
            norm = [("","(aucune UE)")]

        self.id_by_label = {label: uid for uid, label in norm}
        labels = list(self.id_by_label.keys())

        # -- UI
        ctk.CTkLabel(self, text="Choisir une UE", font=("Helvetica", 20, "bold")).pack(pady=(16, 10))
        self.var = ctk.StringVar(value=labels[0])
        self.menu = ctk.CTkOptionMenu(self, values=labels, variable=self.var, width=380, height=40)
        self.menu.pack(pady=(0, 16))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(pady=6)
        ctk.CTkButton(btns, text="Annuler", fg_color="#9E9E9E",
                      command=self._cancel, width=120).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Valider", command=self._ok, width=160).pack(side="left", padx=6)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())

        self.after(100, self._center)

    def _center(self):
        self.update_idletasks()
        m = self.master
        x = m.winfo_rootx() + (m.winfo_width() // 2) - (self.winfo_width() // 2)
        y = m.winfo_rooty() + (m.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")

    def _ok(self):
        label = self.var.get()
        uid = self.id_by_label.get(label)
        self.result = [uid] if uid else []
        if callable(self._on_validate):
            self._on_validate(self.result)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    @staticmethod
    def open(parent, ue_items=None, title="Associer une UE"):
        dlg = UEDialogSingleSelect(parent, ue_items=ue_items, title=title)
        parent.wait_window(dlg)
        return dlg.result or []

# ---------- Dialog multi‑sélection Collèges ----------
class CollegeDialogMultiSelect(ctk.CTkToplevel):
    """
    Liste de collèges avec cases à cocher.
    on_validate(selected: list[str]) est appelé sur 'Valider'.
    """
    def __init__(self, parent, colleges: list[str], on_validate):
        super().__init__(parent)
        self.title("Associer un collège")
        self.configure(fg_color=COLORS.get("card", COLORS["bg_light"]))
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # Dimensions + centrage
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = 520, 420
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # Contenu
        root = ctk.CTkFrame(self, fg_color=COLORS.get("card", COLORS["bg_light"]), corner_radius=0)
        root.pack(fill="both", expand=True, padx=12, pady=12)

        ctk.CTkLabel(
            root, text="Choisir un ou plusieurs collèges",
            font=("SF Pro", 18, "bold"), text_color=COLORS["text_primary"]
        ).pack(pady=(8, 6))

        # Zone scrollable
        list_frame = ctk.CTkFrame(root, fg_color=COLORS.get("card", COLORS["bg_light"]))
        list_frame.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        canvas = ctk.CTkCanvas(
            list_frame, bg=COLORS.get("card", COLORS["bg_light"]), highlightthickness=0
        )
        vsb = ctk.CTkScrollbar(list_frame, orientation="vertical", command=canvas.yview)
        inner = ctk.CTkFrame(canvas, fg_color=COLORS.get("card", COLORS["bg_light"]))

        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Molette active (sans bind_all)
        def _on_mousewheel(event):
            delta = event.delta
            if delta:
                canvas.yview_scroll(-1 if delta > 0 else 1, "units")

        def _on_btn4(event):  # Linux
            canvas.yview_scroll(-1, "units")

        def _on_btn5(event):  # Linux
            canvas.yview_scroll(1, "units")

        def _bind_scroll(w):
            w.bind("<MouseWheel>", _on_mousewheel)  # Windows/macOS
            w.bind("<Button-4>", _on_btn4)          # Linux
            w.bind("<Button-5>", _on_btn5)

        for w in (self, root, list_frame, canvas, inner):
            _bind_scroll(w)

        # Checkboxes
        self._vars = []
        for name in colleges:
            var = ctk.BooleanVar(value=False)
            self._vars.append((name, var))
            ctk.CTkCheckBox(
                inner, text=name, variable=var,
                fg_color=COLORS["accent"],
                hover_color=COLORS.get("accent_hover", COLORS["accent"]),
                text_color=COLORS["text_primary"]
            ).pack(anchor="w", padx=8, pady=4)

        # Boutons
        btns = ctk.CTkFrame(root, fg_color=COLORS.get("card", COLORS["bg_light"]))
        btns.pack(pady=(4, 0))

        ctk.CTkButton(
            btns, text="Annuler", width=160, height=36,
            fg_color=COLORS["bg_light"], text_color=COLORS["text_primary"],
            hover_color="#E6E6E6", command=self.destroy
        ).pack(side="left", padx=6)

        def _submit():
            selected = [name for name, var in self._vars if var.get()]
            try:
                on_validate(selected)
            finally:
                self.destroy()

        ctk.CTkButton(
            btns, text="Valider", width=160, height=36,
            fg_color=COLORS["accent"], text_color="white",
            hover_color=COLORS.get("accent_hover", COLORS["accent"]),
            command=_submit
        ).pack(side="left", padx=6)


