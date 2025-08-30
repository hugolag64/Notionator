from __future__ import annotations
import customtkinter as ctk

class VirtualList(ctk.CTkFrame):
    def __init__(self, parent, row_height: int, render_row, get_count):
        super().__init__(parent)
        self.row_height = row_height
        self.render_row = render_row      # fn(index, parent) -> widget
        self.get_count = get_count        # fn() -> int
        self.canvas = ctk.CTkCanvas(self, highlightthickness=0)
        self.scroll = ctk.CTkScrollbar(self, command=self._yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.inner = ctk.CTkFrame(self.canvas)
        self.win = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        self._mounted: dict[int, ctk.CTkFrame] = {}
        self._last_top = 0
        self.after(0, self._refresh)

    def _on_canvas_resize(self, e):
        self.canvas.itemconfig(self.win, width=e.width)
        self._refresh()

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-1*(e.delta/120)), "units")
        self._refresh()

    def _yview(self, *args):
        self.canvas.yview(*args)
        self._refresh()

    def _refresh(self):
        count = self.get_count()
        total_h = max(count * self.row_height, self.canvas.winfo_height())
        self.inner.configure(height=total_h)

        # fenêtre visible
        first_px = int(self.canvas.canvasy(0))
        last_px  = first_px + self.canvas.winfo_height()
        first_i  = max(first_px // self.row_height - 3, 0)        # marge
        last_i   = min((last_px // self.row_height) + 3, count-1)

        # démonte les lignes hors fenêtre
        for i, w in list(self._mounted.items()):
            if i < first_i or i > last_i:
                w.destroy()
                del self._mounted[i]

        # monte les lignes visibles
        y = first_i * self.row_height
        for i in range(first_i, last_i + 1):
            if i in self._mounted:
                continue
            row = self.render_row(i, self.inner)
            row.place(x=0, y=i*self.row_height, relwidth=1, height=self.row_height)
            self._mounted[i] = row
