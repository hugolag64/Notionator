import customtkinter as ctk

class Skeleton(ctk.CTkFrame):
    def __init__(self, parent, height=56):
        super().__init__(parent, corner_radius=16, fg_color=("gray20","gray90"))
        self._pulse = 0
        self._h = height
        self._bar = ctk.CTkFrame(self, corner_radius=12, height=12, fg_color=("gray30","gray80"))
        self._bar.place(relx=0.02, rely=0.5, anchor="w", relwidth=0.3)
        self.after(16, self._anim)

    def _anim(self):
        self._pulse = (self._pulse + 0.02) % 1.0
        w = 0.25 + 0.25 * (1 + (self._pulse*2-1)**2)  # variation douce
        self._bar.place_configure(relwidth=w)
        self.after(16, self._anim)
