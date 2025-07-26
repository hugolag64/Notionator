import customtkinter as ctk
from ui.sidebar import Sidebar
from ui.dashboard import Dashboard
from core.note_manager import get_all_notes

class MainWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Notionator")
        self.geometry("1000x600")

        self.notes = get_all_notes()

        self.sidebar = Sidebar(self)
        self.sidebar.pack(side="left", fill="y")

        self.dashboard = Dashboard(self)
        self.dashboard.pack(side="right", fill="both", expand=True)
