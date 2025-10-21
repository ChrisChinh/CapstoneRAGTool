from tkinter import *
from tkinter import filedialog, messagebox
from model import Model
import threading


class GUI(Tk):
    def __init__(self):
        super().__init__()
        # Window config
        self.title("Refactor-inator")
        self.geometry("1000x700")
        self.minsize(900, 600)

        # Top bar: title (left) and model status (right)
        self._build_top_bar()

        # Middle: input -> arrow -> output
        self._build_center()

        # Bottom bar: Settings | Run | Load Data
        self._build_bottom_bar()

        self.model = Model()
        

    # ------------------------- UI Builders -------------------------
    def _build_top_bar(self):
        top_bar = Frame(self, padx=20, pady=10)
        top_bar.pack(side=TOP, fill=X)

        title_lb = Label(top_bar, text="IPP Refactor Tool", font=("Arial", 20, "bold"))
        title_lb.grid(row=0, column=0, sticky=W)

        self.model_status_lb = Label(
            top_bar,
            text="Model Status: Connected",
            font=("Arial", 10)
        )
        self.model_status_lb.grid(row=0, column=1, sticky=E)

        top_bar.grid_columnconfigure(0, weight=1)
        top_bar.grid_columnconfigure(1, weight=1)

    def _build_center(self):
        center = Frame(self, padx=20, pady=10)
        center.pack(expand=True, fill=BOTH)

        # Input pane
        input_frame = LabelFrame(center, text="Input", padx=10, pady=10)
        input_frame.grid(row=0, column=0, sticky=NSEW, padx=(0, 10), pady=10)

        self.input_text = Text(input_frame, wrap=WORD, font=("Consolas", 11))
        self.input_text.grid(row=0, column=0, sticky=NSEW)
        input_frame.grid_rowconfigure(0, weight=1)
        input_frame.grid_columnconfigure(0, weight=1)

        # Arrow canvas (visual connection)
        arrow_frame = Frame(center)
        arrow_frame.grid(row=0, column=1, sticky=NS, padx=10, pady=10)
        self.arrow_canvas = Canvas(arrow_frame, width=90, highlightthickness=0)
        self.arrow_canvas.pack(fill=Y, expand=True)
        # Redraw arrow on resize to keep it centered
        self.arrow_canvas.bind("<Configure>", lambda e: self._draw_arrow())

        # Output pane
        output_frame = LabelFrame(center, text="Output", padx=10, pady=10)
        output_frame.grid(row=0, column=2, sticky=NSEW, padx=(10, 0), pady=10)

        self.output_text = Text(output_frame, wrap=WORD, font=("Consolas", 11))
        self.output_text.grid(row=0, column=0, sticky=NSEW)
        output_frame.grid_rowconfigure(0, weight=1)
        output_frame.grid_columnconfigure(0, weight=1)

        # Make center area responsive
        center.grid_rowconfigure(0, weight=1)
        center.grid_columnconfigure(0, weight=1)
        center.grid_columnconfigure(1, weight=0)
        center.grid_columnconfigure(2, weight=1)

    def _build_bottom_bar(self):
        bottom_bar = Frame(self, padx=20, pady=10)
        bottom_bar.pack(side=BOTTOM, fill=X)

        buttons_wrap = Frame(bottom_bar)
        buttons_wrap.pack()

        self.settings_btn = Button(buttons_wrap, text="Settings", width=12, command=self.on_settings)
        self.settings_btn.pack(side=LEFT, padx=8)

        self.run_btn = Button(buttons_wrap, text="Run", width=12, command=self.on_run)
        self.run_btn.pack(side=LEFT, padx=8)

        self.load_btn = Button(buttons_wrap, text="Load Data", width=12, command=self.on_load_data)
        self.load_btn.pack(side=LEFT, padx=8)

    # ------------------------- Helpers -------------------------
    def _draw_arrow(self):
        # Draw a horizontal arrow pointing from Input to Output, centered vertically
        c = self.arrow_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w <= 0 or h <= 0:
            return
        y = h // 2
        margin = 10
        c.create_line(margin, y, w - margin - 10, y, width=3, arrow=LAST, arrowshape=(12, 15, 6))
        # Optional label near the arrow
        # c.create_text(w//2, y - 15, text=">>")

    def _check_model_connection(self):
        def check():
            self.model_status_lb.config(text="Model Status: Checking...")
            try:
                # Simple test query to check connection
                response = self.model.run("Hello")
                if response:
                    self.model_status_lb.config(text="Model Status: Connected")
                else:
                    self.model_status_lb.config(text="Model Status: No response")
            except Exception as e:
                self.model_status_lb.config(text=f"Model Status: Error - {e}")

        threading.Thread(target=check, daemon=True).start()

    # ------------------------- Button Actions -------------------------
    def on_settings(self):
        # One-time system prompt dialog (no persistence)
        dlg = Toplevel(self)
        dlg.title("System Prompt")
        dlg.geometry("700x450")
        dlg.transient(self)
        dlg.grab_set()

        # Container
        container = Frame(dlg, padx=12, pady=12)
        container.pack(fill=BOTH, expand=True)

        Label(container, text="System prompt (applies to this session only):", font=("Arial", 11, "bold")).pack(anchor=W)

        # Text area with scrollbar
        text_frame = Frame(container)
        text_frame.pack(fill=BOTH, expand=True, pady=(6, 10))

        scrollbar = Scrollbar(text_frame)
        scrollbar.pack(side=RIGHT, fill=Y)

        sys_text = Text(text_frame, wrap=WORD, font=("Consolas", 11), yscrollcommand=scrollbar.set)
        sys_text.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.config(command=sys_text.yview)

        # Prefill with current system prompt if available
        try:
            current = getattr(self.model, "system_prompt", "") or ""
        except Exception:
            current = ""
        if current:
            sys_text.insert("1.0", current)

        # Buttons
        btns = Frame(container)
        btns.pack(anchor=E)

        def apply_and_close():
            new_prompt = sys_text.get("1.0", END).strip()
            try:
                # Allow clearing to fall back to default; treat empty as None
                self.model.set_system_prompt(new_prompt)
                self.model_status_lb.config(text="Model Status: System prompt set")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to set system prompt:\n{e}")
                return
            finally:
                dlg.grab_release()
            dlg.destroy()

        def cancel():
            dlg.grab_release()
            dlg.destroy()

        Button(btns, text="Cancel", command=cancel, width=10).pack(side=RIGHT, padx=(8, 0))
        Button(btns, text="Apply", command=apply_and_close, width=12).pack(side=RIGHT)

    def on_run(self):
        # Toplevel placeholder
        tl = Toplevel(self)
        Label(tl, text='Running...')
        tl.grab_set()
        # Placeholder behavior: copy input to output to visualize flow
        text = self.input_text.get("1.0", END)
        self.output_text.delete("1.0", END)
        self.output_text.insert("1.0", "Processing...\n")
        
        def run_model(text):
            answer = self.model.run(text)
            self.output_text.delete("1.0", END)
            self.output_text.insert("1.0", answer)
        t = threading.Thread(target=run_model, args=(text,), daemon=True)

        def check_thread():
            if t.is_alive():
                self.after(100, check_thread)
            else:
                tl.destroy()
        t.start()
        check_thread()

    def on_load_data(self):
        path = filedialog.askopenfilename(title="Load Data", filetypes=[
            ("Text files", "*.txt"),
            ("Markdown", "*.md"),
            ("All files", "*.*"),
        ])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = f.read()
            self.input_text.delete("1.0", END)
            self.input_text.insert("1.0", data)
            self.model_status_lb.config(text=f"Model Status: Loaded '{path.split('/')[-1]}'")
        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to load file:\n{e}")


if __name__ == "__main__":
    app = GUI()
    app.mainloop()