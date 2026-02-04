from tkinter import *
from tkinter import filedialog, messagebox
from azure_client import AzureClient
import threading
import re
import logging


class GUI(Tk):
    def __init__(self):
        logging.basicConfig(level=logging.WARNING, force=True)
        super().__init__()
        # Window config
        self.title("Refactor-inator")
        self.geometry("1000x700")
        self.minsize(900, 600)

        # Instantiate Model and check connection
        config_path = "config/config.yaml"
        self.ix = AzureClient(config_path)

        # Bottom bar: Settings | Run | Load Data | Save Output | NEW: Copy Code
        self._build_bottom_bar()


    def _build_bottom_bar(self):
        bottom_bar = Frame(self, padx=20, pady=10)
        bottom_bar.pack(side=BOTTOM, fill=X)

        buttons_wrap = Frame(bottom_bar)
        buttons_wrap.pack()

        self.run_btn = Button(buttons_wrap, text="Upload Data", width=12, command=self.on_load_data)
        self.run_btn.pack(side=LEFT, padx=8)

        # -----------------------------

        self.recreate_db_btn = Button(buttons_wrap, text="Recreate DB", width=12, command=self.ix.create_index)
        self.recreate_db_btn.pack(side=LEFT, padx=8)

        
    def on_load_data(self):
        path = filedialog.askopenfilename(title="Load Data", filetypes=[
            ("PDF Files", "*.pdf"),
        ])
        if not path:
            return
        self.ix.upload_pdf(path)
        messagebox.showinfo("Load Data", f"Loaded data from:\n{path}. Please restart the application to ensure changes take effect.")


if __name__ == "__main__":
    app = GUI()
    app.mainloop()