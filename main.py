import customtkinter as ctk
from ui.main_window import MainWindow
from config import THEME

if __name__ == "__main__":
    ctk.set_appearance_mode(THEME)
    ctk.set_default_color_theme("blue")
    
    app = MainWindow()
    app.mainloop()
