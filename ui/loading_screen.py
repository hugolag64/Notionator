import customtkinter as ctk
from PIL import Image
import os

class LoadingScreen(ctk.CTkToplevel):
    def __init__(self, parent, messages, width=600, height=400, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        self.overrideredirect(True)
        self.configure(fg_color="#FFFFFF")

        self.messages = messages
        self.current_message = 0

        # Met à jour les dimensions
        self.update_idletasks()

        # Utilise directement l'écran principal pour centrer
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        x = int((screen_width - width) / 2)
        y = int((screen_height - height) / 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

        # Cadre principal pour centrer précisément
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.place(relx=0.5, rely=0.5, anchor="center")

        # Logo
        logo_path = os.path.join(os.path.dirname(__file__), "..", "assets", "logo.png")
        logo_image = ctk.CTkImage(Image.open(logo_path), size=(100, 100))
        logo_label = ctk.CTkLabel(main_frame, image=logo_image, text="")
        logo_label.pack(pady=(0, 20))

        # Message dynamique
        self.message_label = ctk.CTkLabel(
            main_frame,
            text=self.messages[self.current_message],
            font=("Helvetica", 18, "bold"),
            text_color="#333333"
        )
        self.message_label.pack()

    def next_message(self):
        self.current_message += 1
        if self.current_message < len(self.messages):
            self.message_label.configure(text=self.messages[self.current_message])
            self.update()
