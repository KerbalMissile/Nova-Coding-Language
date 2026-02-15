#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

def convert_png_to_ico(png_path, ico_path):
    if not PIL_AVAILABLE:
        return False
    try:
        img = Image.open(png_path)
        img.save(ico_path, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        return True
    except Exception as e:
        print(f"Failed to convert PNG to ICO: {e}")
        return False

def translate_nova_to_csharp(source_code, classname="NovaProgram"):
    lines = source_code.splitlines()
    body_lines = []
    needs_forms = False
    needs_drawing = False
    icon_source = None
    icon_basename = None
    icon_is_png = False

    def add_line(l, indent=2):
        body_lines.append("    " * indent + l)

    def extract_args(header):
        start, end = header.find("("), header.rfind(")")
        if start == -1 or end == -1:
            return []
        inner = header[start + 1:end].strip()
        if not inner:
            return []
        parts = [p.strip() for p in inner.split(",")]
        return parts

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        i += 1
        if not line or line.startswith("//"):
            continue

        if line.startswith("ui_window"):
            needs_forms = True
            needs_drawing = True
            args = extract_args(line)
            title = args[0] if len(args) >= 1 else '"Nova App"'
            w = args[1] if len(args) >= 2 else "400"
            h = args[2] if len(args) >= 3 else "300"
            form_var = f"form_{i}"
            add_line(f"Form {form_var} = new Form();")
            add_line(f"{form_var}.Text = {title};")
            add_line(f"{form_var}.ClientSize = new System.Drawing.Size({w}, {h});")
            while i < len(lines):
                inner_raw = lines[i]
                inner = inner_raw.strip()
                i += 1
                if inner == "}" or inner.startswith("}"):
                    break
                if not inner or inner.startswith("//"):
                    continue
                if inner.startswith("set_icon"):
                    a = extract_args(inner)
                    if a:
                        p = a[0].strip('"')
                        icon_source = p
                        icon_basename = os.path.basename(p)
                        if icon_basename.lower().endswith(".png"):
                            icon_is_png = True
                            icon_basename = os.path.splitext(icon_basename)[0] + ".ico"
                        cs_icon = (
                            "try { string _p = System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "
                            + "\"" + icon_basename + "\"); "
                            + "if(_p.ToLower().EndsWith(\".ico\")) " + form_var + ".Icon = new System.Drawing.Icon(_p); "
                            + "else { var pb = new PictureBox(); pb.Image = System.Drawing.Image.FromFile(_p); pb.SizeMode = PictureBoxSizeMode.Zoom; pb.SetBounds(8,8,64,64); "
                            + form_var + ".Controls.Add(pb); } } catch (Exception ex) { Console.WriteLine(\"Icon load error: \" + ex.Message); }"
                        )
                        add_line(cs_icon)
                elif inner.startswith("label"):
                    a = extract_args(inner)
                    txt = a[0] if a else '""'
                    lbl = f"lbl_{i}"
                    add_line(f"Label {lbl} = new Label() {{ Text = {txt}, AutoSize = true }};")
                    if len(a) >= 3:
                        width = a[3] if len(a) >= 4 else "200"
                        height = a[4] if len(a) >= 5 else "24"
                        add_line(f"{lbl}.SetBounds({a[1]}, {a[2]}, {width}, {height});")
                    else:
                        add_line(f"{lbl}.Location = new System.Drawing.Point(80, 20);")
                    add_line(f"{form_var}.Controls.Add({lbl});")
                elif inner.startswith("button"):
                    a = extract_args(inner)
                    txt = a[0] if a else '"Button"'
                    btn = f"btn_{i}"
                    add_line(f"Button {btn} = new Button() {{ Text = {txt} }};")
                    if len(a) >= 3:
                        add_line(f"{btn}.SetBounds({a[1]}, {a[2]}, 100, 30);")
                    else:
                        add_line(f"{btn}.SetBounds(80, 100, 100, 30);")
                    if "{" in inner or (i < len(lines) and "{" in lines[i]):
                        add_line(f"{btn}.Click += (s, e) => {{")
                        while i < len(lines):
                            cmd_raw = lines[i]
                            cmd = cmd_raw.strip()
                            i += 1
                            if cmd == "}" or cmd.startswith("}"):
                                break
                            if not cmd or cmd.startswith("//"):
                                continue
                            if cmd.startswith("ui_message"):
                                add_line("MessageBox.Show" + cmd[10:] + (";" if not cmd.endswith(";") else ""), 3)
                            elif cmd.startswith("put"):
                                add_line("Console.WriteLine" + cmd[3:] + (";" if not cmd.endswith(";") else ""), 3)
                            elif cmd.startswith("have "):
                                add_line("var " + cmd[5:] + (";" if not cmd.endswith(";") else ""), 3)
                            elif cmd.startswith("Application.Exit"):
                                add_line("Application.Exit();", 3)
                            else:
                                t = cmd
                                if not (t.endswith(";") or t.endswith("{") or t.endswith("}")):
                                    t = t + ";"
                                add_line(t, 3)
                        add_line("};")
                    add_line(f"{form_var}.Controls.Add({btn});")
                else:
                    t = inner
                    if t.startswith("put"):
                        add_line("Console.WriteLine" + t[3:] + (";" if not t.endswith(";") else ""))
                    elif t.startswith("have "):
                        add_line("var " + t[5:] + (";" if not t.endswith(";") else ""))
                    elif t.startswith("ui_message"):
                        add_line("MessageBox.Show" + t[10:] + (";" if not t.endswith(";") else ""))
                    elif t.startswith("pause"):
                        add_line("Console.ReadKey(true);")
                    else:
                        if not (t.endswith(";") or t.endswith("{") or t.endswith("}")):
                            t = t + ";"
                        add_line(t)
            add_line(f"Application.Run({form_var});")
            continue

        if line.startswith("have "):
            add_line("var " + line[5:] + (";" if not line.endswith(";") else ""))
        elif line.startswith("put"):
            add_line("Console.WriteLine" + line[3:] + (";" if not line.endswith(";") else ""))
        elif line.startswith("ui_message"):
            needs_forms = True
            add_line("MessageBox.Show" + line[10:] + (";" if not line.endswith(";") else ""))
        elif line.startswith("pause"):
            add_line("Console.ReadKey(true);")
        elif line.startswith("when"):
            add_line(line.replace("when", "if") + ( " {" if not line.endswith("{") else ""))
        elif "otherwise" in line:
            add_line(line.replace("otherwise", "else"))
        elif line.startswith("while"):
            add_line(line + ( " {" if not line.endswith("{") else ""))
        else:
            t = line
            if not (t.endswith(";") or t.endswith("{") or t.endswith("}")):
                t = t + ";"
            add_line(t)

    code_parts = ["using System;", "using System.IO;"]
    if needs_forms: code_parts.append("using System.Windows.Forms;")
    if needs_drawing: code_parts.append("using System.Drawing;")
    code_parts.append(f"\npublic class {classname} {{")
    code_parts.append("    [STAThread]\n    public static void Main(string[] args) {")
    if needs_forms:
        code_parts.append("        Application.EnableVisualStyles();")
        code_parts.append("        Application.SetCompatibleTextRenderingDefault(false);")
    code_parts.extend(body_lines)
    code_parts.append("    }\n}")
    return "\n".join(code_parts), {"needs_forms": needs_forms, "needs_drawing": needs_drawing, "icon_source_path": icon_source, "icon_basename": icon_basename, "icon_is_png": icon_is_png}

class NovaGUI:
    def __init__(self, root):
        self.root = root
        root.title("Nova Compiler")
        root.geometry("900x700")
        root.configure(bg="#1e1e1e")
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure("TLabel", background="#1e1e1e", foreground="#d4d4d4")
        style.configure("TButton", background="#3c3c3c", foreground="#d4d4d4")
        style.map("TButton", background=[('active', '#555555')], foreground=[('active', '#ffffff')])
        style.configure("TEntry", fieldbackground="#252526", foreground="#d4d4d4")
        style.configure("TFrame", background="#1e1e1e")
        style.configure("TLabelFrame", background="#1e1e1e", foreground="#d4d4d4")
        style.configure("TRadiobutton", background="#1e1e1e", foreground="#d4d4d4")
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.source_dir_var = tk.StringVar(value=self.script_dir)
        self.csc_path_var = tk.StringVar(value="")
        self.extra_refs_var = tk.StringVar(value="")
        left = ttk.Frame(root, width=320)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)
        right = ttk.Frame(root)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=8, pady=8)
        ttk.Label(left, text="Source Folder:").pack(anchor="w")
        src_row = ttk.Frame(left)
        src_row.pack(fill=tk.X, pady=(4, 8))
        ttk.Entry(src_row, textvariable=self.source_dir_var, width=36).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(src_row, text="...", command=self.browse_source).pack(side=tk.LEFT)
        ttk.Label(left, text=".nova files found:").pack(anchor="w", pady=(6, 0))
        self.listbox = tk.Listbox(left, selectmode=tk.EXTENDED, width=46, height=28, bg="#252526", fg="#d4d4d4", borderwidth=0, highlightthickness=1, highlightbackground="#3c3c3c", selectbackground="#0e639c")
        self.listbox.pack(pady=6)
        ttk.Button(left, text="Refresh List", command=self.refresh_list).pack(fill=tk.X)
        opts = ttk.LabelFrame(right, text="Compile Settings", padding=(8, 8))
        opts.pack(fill=tk.X)
        self.target_var = tk.StringVar(value="exe")
        ttk.Radiobutton(opts, text="Standalone App (.exe)", variable=self.target_var, value="exe").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(opts, text="Standard Library (.dll)", variable=self.target_var, value="dll").grid(row=1, column=0, sticky="w")
        ttk.Label(opts, text="Output Class/Name:").grid(row=0, column=1, sticky="w", padx=(12, 2))
        self.classname_var = tk.StringVar(value="NovaProgram")
        ttk.Entry(opts, textvariable=self.classname_var, width=36).grid(row=0, column=2, sticky="w")
        ttk.Label(opts, text="csc.exe path (optional):").grid(row=1, column=1, sticky="w", padx=(12, 2))
        ttk.Entry(opts, textvariable=self.csc_path_var, width=36).grid(row=1, column=2, sticky="w")
        ttk.Button(opts, text="Browse", command=self.browse_csc).grid(row=1, column=3, sticky="w", padx=6)
        ttk.Label(opts, text="Extra references (semicolon separated):").grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Entry(opts, textvariable=self.extra_refs_var, width=80).grid(row=3, column=0, columnspan=4, sticky="w")
        btn_row = ttk.Frame(right)
        btn_row.pack(fill=tk.X, pady=8)
        ttk.Button(btn_row, text="COMPILE SELECTED", command=self.on_compile).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Open Folder", command=self.open_output).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Show Generated C#", command=self.show_last_cs).pack(side=tk.LEFT)
        self.console = tk.Text(right, bg="black", fg="#00ff00", font=("Consolas", 10), borderwidth=0)
        self.console.pack(fill=tk.BOTH, expand=True)
        self._last_generated_cs = None
        self.refresh_list()

    def browse_source(self):
        d = filedialog.askdirectory()
        if d: self.source_dir_var.set(d); self.refresh_list()

    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        path = self.source_dir_var.get()
        if os.path.exists(path):
            for f in sorted([f for f in os.listdir(path) if f.endswith(".nova")]): self.listbox.insert(tk.END, f)

    def open_output(self):
        folder = self.source_dir_var.get()
        if os.name == 'nt': os.startfile(folder)
        else: subprocess.run(['xdg-open' if os.name != 'posix' else 'open', folder])

    def browse_csc(self):
        p = filedialog.askopenfilename(title="Select csc.exe")
        if p: self.csc_path_var.set(p)

    def show_last_cs(self):
        if not self._last_generated_cs: return
        win = tk.Toplevel(self.root); win.title("Generated C#")
        txt = tk.Text(win, bg="#1e1e1e", fg="#d4d4d4"); txt.pack(fill=tk.BOTH, expand=True)
        txt.insert(tk.END, self._last_generated_cs)

    def on_compile(self):
        sel = self.listbox.curselection()
        if not sel: return
        csc_path = self.find_csc(self.csc_path_var.get().strip() or None)
        if not csc_path: messagebox.showerror("Error", "csc.exe not found."); return
        for i in sel:
            filename = self.listbox.get(i)
            full_path = os.path.join(self.source_dir_var.get(), filename)
            with open(full_path, "r", encoding="utf-8") as f: src = f.read()
            csharp_code, meta = translate_nova_to_csharp(src, self.classname_var.get() or "NovaProgram")
            self._last_generated_cs = csharp_code
            cs_temp = os.path.join(self.source_dir_var.get(), "temp_build.cs")
            with open(cs_temp, "w", encoding="utf-8") as f: f.write(csharp_code)
            out_name = filename.replace(".nova", ".dll" if self.target_var.get() == "dll" else ".exe")
            out_path = os.path.join(self.source_dir_var.get(), out_name)
            if meta.get("icon_source_path"):
                src_icon = os.path.join(self.source_dir_var.get(), meta["icon_source_path"]) if not os.path.isabs(meta["icon_source_path"]) else meta["icon_source_path"]
                if os.path.exists(src_icon):
                    dst = os.path.join(self.source_dir_var.get(), meta["icon_basename"])
                    if meta.get("icon_is_png") and PIL_AVAILABLE: convert_png_to_ico(src_icon, dst)
                    else: shutil.copyfile(src_icon, dst)
            t_flag = "/target:library" if self.target_var.get() == "dll" else ("/target:winexe" if meta.get("needs_forms") else "/target:exe")
            cmd = [csc_path, t_flag, f"/out:{out_path}", cs_temp]
            refs = []
            if meta.get("needs_forms"): refs.append("System.Windows.Forms.dll")
            if meta.get("needs_drawing"): refs.append("System.Drawing.dll")
            if refs: cmd.insert(2, f"/reference:{';'.join(refs)}")
            extra = self.extra_refs_var.get().strip()
            if extra: cmd.append(f"/reference:{extra}")
            self.console.insert(tk.END, f"Compiling {filename}...\n")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0: self.console.insert(tk.END, f"SUCCESS: {out_name}\n")
            else: self.console.insert(tk.END, f"FAILED:\n{proc.stdout}\n{proc.stderr}\n")
            if os.path.exists(cs_temp): os.remove(cs_temp)

    def find_csc(self, custom=None):
        if custom and os.path.exists(custom): return custom
        for c in [shutil.which("csc"), r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe", r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"]:
            if c and os.path.exists(c): return c
        return None

if __name__ == "__main__":
    root = tk.Tk(); app = NovaGUI(root); root.mainloop()