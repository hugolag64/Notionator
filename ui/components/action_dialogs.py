import customtkinter as ctk

class UEDialogSingleSelect(ctk.CTkToplevel):
    def __init__(self, parent, items: list[tuple[str,str]], on_validate):
        super().__init__(parent); self.title("Associer une UE"); self.geometry("420x360"); self.configure(fg_color="#111214")
        self._on_validate = on_validate
        ctk.CTkLabel(self, text="Choisir une UE", font=("SF Pro", 18, "bold")).pack(pady=12)
        self._map = {label: _id for label, _id in items}
        self._var = ctk.StringVar(value=(items[0][0] if items else ""))
        ctk.CTkOptionMenu(self, values=list(self._map.keys()), variable=self._var, width=360).pack(pady=10)
        row = ctk.CTkFrame(self, fg_color="transparent"); row.pack(pady=16)
        ctk.CTkButton(row, text="Annuler", command=self.destroy).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Valider", command=self._submit).pack(side="left", padx=6)
        self.grab_set(); self.focus()

    def _submit(self):
        v = self._var.get(); _id = self._map.get(v)
        if _id: self._on_validate(_id)
        self.destroy()

class CollegeDialogMultiSelect(ctk.CTkToplevel):
    def __init__(self, parent, colleges: list[str], on_validate):
        super().__init__(parent); self.title("Associer des Collèges"); self.geometry("480x420"); self.configure(fg_color="#111214")
        self._on_validate = on_validate; self._vars = {}
        ctk.CTkLabel(self, text="Sélectionner les collèges", font=("SF Pro", 18, "bold")).pack(pady=12)
        frame = ctk.CTkScrollableFrame(self, width=420, height=280, fg_color="#0C0D0F"); frame.pack(padx=16, pady=8)
        for name in colleges:
            var = ctk.BooleanVar(value=False); ctk.CTkCheckBox(frame, text=name, variable=var).pack(anchor="w", pady=4); self._vars[name]=var
        row = ctk.CTkFrame(self, fg_color="transparent"); row.pack(pady=16)
        ctk.CTkButton(row, text="Annuler", command=self.destroy).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Valider", command=self._submit).pack(side="left", padx=6)
        self.grab_set(); self.focus()

    def _submit(self):
        sel = [n for n,v in self._vars.items() if v.get()]
        self._on_validate(sel); self.destroy()
