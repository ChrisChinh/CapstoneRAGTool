from tkinter import *
from tkinter import filedialog, messagebox
from model import Model
import threading
import re


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

        # Bottom bar: Settings | Run | Load Data | Save Output | NEW: Copy Code
        self._build_bottom_bar()

        # Instantiate Model and check connection
        self.model = Model() 
        self._check_model_connection() 
        

    # ------------------------- UI Builders -------------------------
    def _build_top_bar(self):
        top_bar = Frame(self, padx=20, pady=10)
        top_bar.pack(side=TOP, fill=X)

        title_lb = Label(top_bar, text="IPP Refactor Tool", font=("Arial", 20, "bold"))
        title_lb.grid(row=0, column=0, sticky=W)

        self.model_status_lb = Label(
            top_bar,
            text="Model Status: Initializing...",
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
        self.arrow_canvas.bind("<Configure>", lambda e: self._draw_arrow())

        # Output pane
        output_frame = LabelFrame(center, text="Output", padx=10, pady=10)
        output_frame.grid(row=0, column=2, sticky=NSEW, padx=(10, 0), pady=10)

        self.output_text = Text(output_frame, wrap=WORD, font=("Arial", 11)) 
        self.output_text.grid(row=0, column=0, sticky=NSEW)
        output_frame.grid_rowconfigure(0, weight=1)
        output_frame.grid_columnconfigure(0, weight=1)
        
        # --- Define Markdown Style Tags for the output_text widget ---
        self.output_text.tag_configure("bold", font=("Arial", 11, "bold"))
        self.output_text.tag_configure("italic", font=("Arial", 11, "italic"))
        self.output_text.tag_configure("code", 
                                       font=("Consolas", 10), 
                                       background="#f0f0f0", 
                                       relief=FLAT) 
        self.output_text.tag_configure("h1", font=("Arial", 16, "bold"))
        # -----------------------------------------------------------

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
        
        self.save_btn = Button(buttons_wrap, text="Save Output", width=12, command=self.on_save_output)
        self.save_btn.pack(side=LEFT, padx=8)
        
        # --- NEW: Copy Code Button ---
        self.copy_code_btn = Button(buttons_wrap, text="Copy Code", width=12, command=self.on_copy_code)
        self.copy_code_btn.pack(side=LEFT, padx=8)
        # -----------------------------

    # ------------------------- Markdown Rendering -------------------------
    def _apply_markdown_tags(self, text_widget: Text, markdown_text: str):
        """Parses simple Markdown and applies Tkinter tags."""
        
        # 1. Clear existing content and tags
        text_widget.delete("1.0", END)
        
        # 2. Split text by code blocks (```)
        parts = markdown_text.split("```")
        
        for i, part in enumerate(parts):
            if i % 2 == 1:
                # This is a code block (odd-indexed part)
                lines = part.split('\n')
                code_content = '\n'.join(lines[1:]) if lines and lines[0].strip().isalpha() else part
                
                # Insert code block content with 'code' tag
                text_widget.insert(END, code_content, "code")
                text_widget.insert(END, "\n\n")

            else:
                # This is regular text (even-indexed part)
                
                # --- Block-level parsing (Headings) ---
                lines = part.split('\n')
                for line in lines:
                    line = line.strip()
                    if line.startswith("# "):
                        # H1 Heading
                        text_widget.insert(END, line.strip("# ").strip(), "h1")
                        text_widget.insert(END, "\n")
                    else:
                        # --- Inline parsing (Bold/Italic) ---
                        
                        segments = [(line, None)]
                        
                        # Process BOLD (**text**)
                        bold_pattern = r'\*\*(.*?)\*\*'
                        new_segments = []
                        for text, tag in segments:
                            if tag is None:
                                parts_re = re.split(bold_pattern, text)
                                for j, part_re in enumerate(parts_re):
                                    if part_re:
                                        if j % 2 == 1:
                                            new_segments.append((part_re, "bold"))
                                        else:
                                            new_segments.append((part_re, None))
                            else:
                                new_segments.append((text, tag))
                        segments = new_segments
                        
                        # Process ITALIC (*text*)
                        italic_pattern = r'\*(.*?)\*'
                        new_segments = []
                        for text, tag in segments:
                            if tag is None:
                                parts_re = re.split(italic_pattern, text)
                                for j, part_re in enumerate(parts_re):
                                    if part_re:
                                        if j % 2 == 1:
                                            new_segments.append((part_re, "italic"))
                                        else:
                                            new_segments.append((part_re, None))
                            else:
                                new_segments.append((text, tag))
                        segments = new_segments
                        
                        # Insert all segments into the widget
                        for text, tag in segments:
                            if tag:
                                text_widget.insert(END, text, tag)
                            else:
                                text_widget.insert(END, text)
                        
                        text_widget.insert(END, "\n")


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

    def _check_model_connection(self):
        def check():
            self.model_status_lb.config(text="Model Status: Checking...")
            try:
                response = self.model.check_connection()
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
        # Toplevel placeholder (for blocking GUI while running)
        tl = Toplevel(self)
        Label(tl, text='Running...').pack(padx=20, pady=20)
        tl.title("Processing")
        tl.grab_set()
        
        text = self.input_text.get("1.0", END)
        self.output_text.delete("1.0", END)
        self.output_text.insert("1.0", "Processing...\n")
        
        def run_model():
            try:
                answer = self.model.run(text)
                self.after(0, lambda: self._apply_markdown_tags(self.output_text, answer))
            except Exception as e:
                self.after(0, lambda: self.output_text.insert("1.0", f"Error during model run: {e}"))
            finally:
                self.after(0, tl.destroy)

        t = threading.Thread(target=run_model, daemon=True)
        t.start()
        
    def on_load_data(self):
        path = filedialog.askopenfilename(title="Load Data", filetypes=[
            ("PDF Files", "*.pdf"),
        ])
        if not path:
            return
        self.model.add_pdf_to_rag(path)
        messagebox.showinfo("Load Data", f"Loaded data from:\n{path}. Please restart the application to ensure changes take effect.")

    def on_save_output(self):
        output_content = self.output_text.get("1.0", END).strip()
        
        if not output_content or output_content == "Processing...":
            messagebox.showwarning("Save Warning", "The output box is empty or still processing.")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[
                ("Markdown files", "*.md"),
                ("Text files", "*.txt"),
                ("All files", "*.*")
            ],
            title="Save Refactored Code"
        )

        if not path:
            return 

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(output_content)
            
            messagebox.showinfo("Save Success", f"Output successfully saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save file:\n{e}")

    # --- NEW METHOD: Copies all text tagged with "code" to the clipboard ---
    def on_copy_code(self):
        code_ranges = self.output_text.tag_ranges("code")
        
        if not code_ranges:
            messagebox.showwarning("Copy Warning", "No code block found in the output.")
            return
            
        full_code = []
        
        # tag_ranges returns a tuple of index pairs (start, end, start, end, ...)
        # We iterate over the list in pairs to get the start and end indices for each block
        for i in range(0, len(code_ranges), 2):
            start_index = code_ranges[i]
            end_index = code_ranges[i+1]
            
            # Get the content for this specific range
            code_block = self.output_text.get(start_index, end_index)
            full_code.append(code_block.strip())

        final_code_to_copy = "\n\n".join(full_code)

        # Use the root window's clipboard methods
        try:
            self.clipboard_clear()
            self.clipboard_append(final_code_to_copy)
            self.update() # Forces the clipboard update
            messagebox.showinfo("Copy Success", "Code block(s) copied to clipboard.")
        except TclError as e:
            messagebox.showerror("Copy Error", f"Failed to copy to clipboard: {e}")
        except Exception as e:
            messagebox.showerror("Copy Error", f"An unexpected error occurred: {e}")
            
    # ------------------------------------------------------------------


if __name__ == "__main__":
    app = GUI()
    app.mainloop()