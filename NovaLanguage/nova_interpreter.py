#!/usr/bin/env python3
"""
nova_interpreter.py  —  Nova REPL + file runner
================================================
Complete feature parity with nova_compiler.py (minus .NET IL / GUI-only things).

EXPRESSIONS
  int / float / string literals     42  3.14  "hello"
  variables                         x  myVar
  arithmetic                        + - * /   (/ always true-division → float)
  modulo                            %
  chained ops                       a + b + c + d
  comparisons → 0/1                 == != < > <= >=
  string concat                     "hi " + name
  unary minus                       -x  -3.14
  parentheses                       (a + b) * c
  array index                       arr[n]  (n = any expression)
  built-in calls                    len  int  float  str  abs  max  min  type
                                    read_file  write_file  input  put

STATEMENTS
  // comment          line comment
  ; separator         same as newline
  have x = expr       declare + assign  (arrays:  have x = ["a","b"])
  x = expr            assign (auto-declare)
  x[n] = expr         array element assign
  put(expr)           print to output
  when(expr){…}       if-block  (else = otherwise{…})
  otherwise{…}        else-block (after when)
  while(expr){…}      loop while truthy
  repeat N {…}        loop exactly N times
  break               exit innermost loop
  write_file(p,expr)  write string to file
  input(prompt)       read line from stdin → string
  multi-stmt line     two-or-more spaces between statements
  inline blocks       when(x){put(1)}otherwise{put(0)}  on one line
"""

import re, sys, os
import tkinter as tk
from tkinter import scrolledtext, filedialog, messagebox


# ─── tokeniser ────────────────────────────────────────────────────────────────

TOKEN_SPEC = [
    ('FLOAT',     r'\d+\.\d+'),        # must come before INT
    ('INT',       r'\d+'),
    ('STRING',    r'"[^"]*"'),
    ('ID',        r'[A-Za-z_]\w*'),
    ('OP',        r'==|!=|<=|>=|[+\-*/%<>]'),
    ('EQ',        r'='),
    ('LBRACE',    r'\{'), ('RBRACE',  r'\}'),
    ('LPAREN',    r'\('), ('RPAREN',  r'\)'),
    ('LBRACK',    r'\['), ('RBRACK',  r'\]'),
    ('COMMA',     r','),
    ('SEMI',      r';'),
    ('SKIP',      r'[ \t\r\n]+'),
    ('COMMENT',   r'//[^\n]*'),
    ('MISMATCH',  r'.'),
]
_TOK_RE = re.compile('|'.join(f'(?P<{k}>{v})' for k, v in TOKEN_SPEC))

def tokenize(code):
    toks = []
    for m in _TOK_RE.finditer(code):
        k, v = m.lastgroup, m.group()
        if k in ('SKIP', 'COMMENT', 'MISMATCH'): continue
        if   k == 'FLOAT':  v = float(v)
        elif k == 'INT':    v = int(v)
        elif k == 'STRING': v = v[1:-1]
        toks.append({'type': k, 'val': v})
    return toks


# ─── preprocessor (multi-stmt + inline-block expander) ───────────────────────
# Mirrors the compiler's preprocess() so .nova files behave identically.

def _strip_comment(s):
    out, in_str = [], False
    i = 0
    while i < len(s):
        c = s[i]
        if   c == '"' and not in_str: in_str = True;  out.append(c)
        elif c == '"' and in_str:     in_str = False; out.append(c)
        elif c == '/' and not in_str and i+1 < len(s) and s[i+1] == '/': break
        else: out.append(c)
        i += 1
    return ''.join(out).rstrip()

def _split_stmts(s):
    """Split on 2+ spaces or ; outside parens/brackets/quotes.
    'use' lines are never split.  Multiple 'have' split on '  have' boundary."""
    st = s.strip()
    if st.startswith('use '): return [st]
    if st.startswith('have '):
        parts = re.split(r'  +(?=have )', st)
        return [p.strip() for p in parts if p.strip()]
    parts, cur = [], ''
    dp = db = 0; in_str = False; i = 0
    while i < len(s):
        c = s[i]
        if   c == '"' and not in_str: in_str = True;  cur += c
        elif c == '"' and in_str:     in_str = False; cur += c
        elif not in_str and c == '(':  dp += 1; cur += c
        elif not in_str and c == ')':  dp -= 1; cur += c
        elif not in_str and c == '[':  db += 1; cur += c
        elif not in_str and c == ']':  db -= 1; cur += c
        elif not in_str and dp == 0 and db == 0 and c == ';':
            if cur.strip(): parts.append(cur.strip())
            cur = ''
        elif (not in_str and dp == 0 and db == 0
              and c == ' ' and i+1 < len(s) and s[i+1] == ' '):
            if cur.strip(): parts.append(cur.strip())
            cur = ''
            while i < len(s) and s[i] == ' ': i += 1
            continue
        else: cur += c
        i += 1
    if cur.strip(): parts.append(cur.strip())
    return parts or [s.strip()]

def _expand_line(s, base_indent):
    if '{' not in s and '}' not in s:
        return [' '*base_indent + p for p in _split_stmts(s) if p.strip()]
    result = []; cur = ''; depth = 0; in_str = False; dp = 0
    for ch in s:
        if   ch == '"' and not in_str: in_str = True;  cur += ch
        elif ch == '"' and in_str:     in_str = False; cur += ch
        elif not in_str and ch == '(':  dp += 1; cur += ch
        elif not in_str and ch == ')':  dp -= 1; cur += ch
        elif not in_str and dp == 0 and ch == '{':
            for sub in _split_stmts(cur):
                if sub.strip(): result.append(' '*(base_indent + depth*4) + sub.strip())
            cur = ''; depth += 1
        elif not in_str and dp == 0 and ch == '}':
            for sub in _split_stmts(cur):
                if sub.strip(): result.append(' '*(base_indent + depth*4) + sub.strip())
            cur = ''; depth = max(0, depth-1)
        else: cur += ch
    for sub in _split_stmts(cur):
        if sub.strip(): result.append(' '*base_indent + sub.strip())
    return result

def preprocess(source):
    """Expand multi-stmt lines and inline blocks, strip comments."""
    lines = []
    for ln in source.splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith('//'): continue
        indent = len(ln) - len(ln.lstrip())
        clean  = _strip_comment(stripped)
        if not clean: continue
        for el in _expand_line(clean, indent):
            if el.strip(): lines.append(el)
    return '\n'.join(lines)


# ─── interpreter ─────────────────────────────────────────────────────────────

class BreakSignal(Exception): pass

class NovaInterpreter:
    def __init__(self, tokens, output_fn=None):
        self.tokens = tokens
        self.pos    = 0
        self.vars   = {}
        self._out   = output_fn or (lambda s: print(s))

    # ── token helpers ─────────────────────────────────────────────────────────

    def peek(self, off=0):
        i = self.pos + off
        return self.tokens[i] if i < len(self.tokens) else None

    def eat(self, typ=None, val=None):
        t = self.peek()
        if t is None: raise SyntaxError("Unexpected end of input")
        if typ and t['type'] != typ:
            raise SyntaxError(f"Expected {typ}, got {t['type']} ({t['val']!r})")
        if val is not None and t['val'] != val:
            raise SyntaxError(f"Expected {val!r}, got {t['val']!r}")
        self.pos += 1; return t

    def match(self, typ=None, val=None):
        t = self.peek()
        if not t: return False
        if typ and t['type'] != typ: return False
        if val is not None and t['val'] != val: return False
        return True

    # ── top-level runner ──────────────────────────────────────────────────────

    def run_all(self):
        while self.pos < len(self.tokens):
            self.statement()

    def statement(self):
        t = self.peek()
        if not t or t['val'] == '}': return

        # ── ; no-op ──────────────────────────────────────────────────────────
        if t['type'] == 'SEMI': self.eat(); return

        # ── have NAME = rhs ──────────────────────────────────────────────────
        if t['type'] == 'ID' and t['val'] == 'have':
            self.eat()
            name = self.eat('ID')['val']
            self.eat('EQ')
            self.vars[name] = self._rhs()
            return

        # ── break ─────────────────────────────────────────────────────────────
        if t['type'] == 'ID' and t['val'] == 'break':
            self.eat(); raise BreakSignal()

        # ── put(expr) ─────────────────────────────────────────────────────────
        if t['type'] == 'ID' and t['val'] == 'put':
            self.eat(); self.eat('LPAREN')
            self._out(self._display(self.expression()))
            self.eat('RPAREN'); return

        # ── write_file("path", expr) ──────────────────────────────────────────
        if t['type'] == 'ID' and t['val'] == 'write_file':
            self.eat(); self.eat('LPAREN')
            path = self.eat('STRING')['val']; self.eat('COMMA')
            val  = self.expression();         self.eat('RPAREN')
            with open(path, 'w', encoding='utf-8') as f: f.write(str(val))
            return

        # ── when(expr){…} [otherwise{…}] ─────────────────────────────────────
        if t['type'] == 'ID' and t['val'] == 'when':
            self.eat(); self.eat('LPAREN')
            cond = self.expression(); self.eat('RPAREN')
            body  = self._block()
            other = None
            if self.match('ID', 'otherwise'): self.eat(); other = self._block()
            if self._truthy(cond): self._exec_block(body)
            elif other is not None: self._exec_block(other)
            return

        # ── while(expr){…} ───────────────────────────────────────────────────
        if t['type'] == 'ID' and t['val'] == 'while':
            self.eat(); self.eat('LPAREN')
            cond_toks = self._collect_until('RPAREN'); self.eat('RPAREN')
            body = self._block()
            while True:
                if not self._truthy(self._eval_tokens(cond_toks)): break
                try: self._exec_block(body)
                except BreakSignal: break
            return

        # ── repeat N {…} ─────────────────────────────────────────────────────
        if t['type'] == 'ID' and t['val'] == 'repeat':
            self.eat()
            n    = int(self._coerce_int(self.expression()))
            body = self._block()
            for _ in range(n):
                try: self._exec_block(body)
                except BreakSignal: break
            return

        # ── NAME[n] = val  or  NAME = val  or  standalone expr ───────────────
        if t['type'] == 'ID':
            name = t['val']
            nxt  = self.peek(1)
            if nxt and nxt['type'] == 'LBRACK':          # NAME[n] = val
                self.eat(); self.eat()
                idx = self.expression(); self.eat('RBRACK'); self.eat('EQ')
                val = self.expression()
                arr = self.vars.get(name, [])
                if isinstance(arr, list):
                    i = int(self._coerce_int(idx))
                    while len(arr) <= i: arr.append("")
                    arr[i] = val; self.vars[name] = arr
                return
            if nxt and nxt['type'] == 'EQ':               # NAME = val
                self.eat(); self.eat()
                self.vars[name] = self.expression(); return
            self.expression(); return                      # standalone expr

        self.expression()   # fallback

    # ── block helpers ─────────────────────────────────────────────────────────

    def _rhs(self):
        """Array literal  [a, b, c]  or plain expression."""
        if self.match('LBRACK'):
            self.eat(); items = []
            while self.peek() and not self.match('RBRACK'):
                items.append(self.expression())
                if self.match('COMMA'): self.eat()
            self.eat('RBRACK'); return items
        return self.expression()

    def _block(self):
        """Consume { … } and return the inner token slice."""
        self.eat('LBRACE'); start = self.pos; depth = 1
        while self.pos < len(self.tokens) and depth:
            k = self.tokens[self.pos]['type']
            if k == 'LBRACE': depth += 1
            elif k == 'RBRACE': depth -= 1
            self.pos += 1
        return self.tokens[start: self.pos - 1]

    def _exec_block(self, toks):
        saved, self.tokens, self.pos = (self.pos, self.tokens), toks, 0
        try: self.run_all()
        finally: self.tokens, self.pos = saved

    def _collect_until(self, stop_type):
        """Collect tokens (respecting paren depth) until stop_type at depth 0."""
        toks = []; depth = 0
        while self.pos < len(self.tokens):
            t = self.tokens[self.pos]
            if t['type'] == 'LPAREN': depth += 1
            elif t['type'] == 'RPAREN':
                if depth == 0: break
                depth -= 1
            toks.append(t); self.pos += 1
        return toks

    def _eval_tokens(self, toks):
        """Evaluate a token list as an expression using shared vars."""
        saved, self.tokens, self.pos = (self.pos, self.tokens), toks, 0
        try: return self.expression()
        finally: self.tokens, self.pos = saved

    # ── expression parser (precedence climbing) ───────────────────────────────

    _PREC = [
        {'==', '!=', '<', '>', '<=', '>='},   # 0 comparison
        {'+', '-'},                            # 1 additive
        {'*', '/', '%'},                       # 2 multiplicative
    ]

    def expression(self):
        return self._prec(0)

    def _prec(self, lvl):
        if lvl >= len(self._PREC): return self._unary()
        ops  = self._PREC[lvl]
        left = self._prec(lvl + 1)
        while self.match('OP') and self.peek()['val'] in ops:
            op    = self.eat()['val']
            right = self._prec(lvl + 1)
            left  = self._op(op, left, right)
        return left

    def _unary(self):
        if self.match('OP', '-'):
            self.eat(); v = self._primary()
            return -v if isinstance(v, (int, float)) else v
        return self._primary()

    def _primary(self):
        t = self.peek()
        if not t: return 0

        if t['type'] == 'LPAREN':
            self.eat(); v = self.expression(); self.eat('RPAREN'); return v
        if t['type'] == 'FLOAT':  self.eat(); return t['val']
        if t['type'] == 'INT':    self.eat(); return t['val']
        if t['type'] == 'STRING': self.eat(); return t['val']

        if t['type'] == 'ID':
            name = t['val']; self.eat()

            # ── built-in call ────────────────────────────────────────────────
            if self.match('LPAREN'):
                self.eat()
                # helpers
                def _arg():  return self.expression()
                def _arg2(): a = _arg(); self.eat('COMMA'); b = _arg(); return a, b
                def _close(): self.eat('RPAREN')

                if name == 'put':
                    v = _arg(); _close(); self._out(self._display(v)); return v
                if name == 'read_file':
                    path = self.eat('STRING')['val']; _close()
                    try:
                        with open(path, encoding='utf-8') as f: return f.read()
                    except Exception as e: return f"ERROR:{e}"
                if name == 'input':
                    prompt = '' if self.match('RPAREN') else self._display(_arg())
                    _close(); return input(prompt)
                if name == 'len':
                    v = _arg(); _close()
                    return len(v) if isinstance(v, (str, list)) else 0
                if name == 'int':
                    v = _arg(); _close(); return self._coerce_int(v)
                if name == 'float':
                    v = _arg(); _close(); return self._coerce_float(v)
                if name == 'str':
                    v = _arg(); _close(); return self._display(v)
                if name == 'abs':
                    v = _arg(); _close()
                    return abs(v) if isinstance(v, (int, float)) else v
                if name == 'max':
                    a, b = _arg2(); _close(); return max(a, b)
                if name == 'min':
                    a, b = _arg2(); _close(); return min(a, b)
                if name == 'type':
                    v = _arg(); _close()
                    if isinstance(v, list):  return "array"
                    if isinstance(v, float): return "float"
                    if isinstance(v, int):   return "int"
                    return "string"
                if name == 'write_file':
                    path = self.eat('STRING')['val']; self.eat('COMMA')
                    v = _arg(); _close()
                    with open(path, 'w', encoding='utf-8') as f: f.write(str(v))
                    return v
                # unknown — eat args, return 0
                while not self.match('RPAREN') and self.peek():
                    _arg()
                    if self.match('COMMA'): self.eat()
                _close(); return 0

            # ── array index ──────────────────────────────────────────────────
            if self.match('LBRACK'):
                self.eat(); idx = self.expression(); self.eat('RBRACK')
                arr = self.vars.get(name, [])
                if isinstance(arr, list):
                    i = int(self._coerce_int(idx))
                    return arr[i] if 0 <= i < len(arr) else ""
                return ""

            return self.vars.get(name, 0)   # plain variable

        return 0

    # ── operators ─────────────────────────────────────────────────────────────

    def _op(self, op, l, r):
        if op == '+' and (isinstance(l, str) or isinstance(r, str)):
            return self._display(l) + self._display(r)
        if op == '+':  return l + r
        if op == '-':  return l - r
        if op == '*':  return l * r
        if op == '/':
            if r == 0: return 0
            res = l / r; return int(res) if res == int(res) else res
        if op == '%':  return int(l) % int(r)
        if op == '==': return int(l == r)
        if op == '!=': return int(l != r)
        if op == '<':  return int(l <  r)
        if op == '>':  return int(l >  r)
        if op == '<=': return int(l <= r)
        if op == '>=': return int(l >= r)
        return 0

    # ── helpers ───────────────────────────────────────────────────────────────

    def _truthy(self, v):
        if isinstance(v, (int, float)): return v != 0
        if isinstance(v, str):          return v != ""
        if isinstance(v, list):         return len(v) > 0
        return bool(v)

    def _display(self, v):
        if isinstance(v, float) and v == int(v): return str(int(v))
        if isinstance(v, list): return "[" + ", ".join(self._display(x) for x in v) + "]"
        return str(v)

    def _coerce_int(self, v):
        try: return int(float(str(v)))
        except: return 0

    def _coerce_float(self, v):
        try: return float(str(v))
        except: return 0.0


# ─── public runner ────────────────────────────────────────────────────────────

def run_nova(source, output_fn=None):
    """Preprocess (expand multi-stmt / inline blocks) then interpret."""
    expanded = preprocess(source)
    tokens   = tokenize(expanded)
    interp   = NovaInterpreter(tokens, output_fn)
    interp.run_all()
    return interp


# ─── terminal UI ─────────────────────────────────────────────────────────────

class NovaInterpreterApp:
    def __init__(self, root):
        self.root = root
        root.title("Nova Interpreter")
        root.geometry("820x580")
        self.BG = "#0d0d0d"; self.FG = "#00ff88"
        self.BTN = "#1a1a2e"; self.EBG = "#111111"
        root.configure(bg=self.BG)
        self._vars   = {}
        self._history = []; self._hidx = 0

        # toolbar
        bar = tk.Frame(root, bg=self.BG, padx=6, pady=4); bar.pack(fill="x")
        for label, cmd in [("Run File", self._run_file),
                            ("Clear",    self._clear),
                            ("Reset Vars", self._reset_vars)]:
            tk.Button(bar, text=label, command=cmd,
                      bg=self.BTN, fg=self.FG, relief="flat", padx=8).pack(side="left", padx=3)

        # output area
        self.terminal = scrolledtext.ScrolledText(
            root, bg=self.BG, fg=self.FG, insertbackground=self.FG,
            font=("Consolas", 11), relief="flat", borderwidth=0)
        self.terminal.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        self._emit("Nova Interpreter  —  type Nova code and press Enter\n")

        # input row
        row = tk.Frame(root, bg=self.BG, padx=8, pady=6); row.pack(fill="x")
        tk.Label(row, text="›", bg=self.BG, fg=self.FG,
                 font=("Consolas", 13)).pack(side="left")
        self.entry = tk.Entry(row, bg=self.EBG, fg=self.FG,
                              insertbackground=self.FG, font=("Consolas", 11),
                              relief="flat", borderwidth=4)
        self.entry.pack(side="left", fill="x", expand=True, padx=6)
        self.entry.bind("<Return>", self._run_line)
        self.entry.bind("<Up>",     self._hist_up)
        self.entry.bind("<Down>",   self._hist_down)
        self.entry.focus_set()

    def _emit(self, msg):
        self.terminal.insert(tk.END, str(msg) + "\n")
        self.terminal.see(tk.END)

    def _run_line(self, _=None):
        code = self.entry.get().strip()
        if not code: return
        self._history.append(code); self._hidx = len(self._history)
        self.terminal.insert(tk.END, f"› {code}\n")
        self.entry.delete(0, tk.END)
        self._exec(code)

    def _exec(self, code):
        try:
            expanded = preprocess(code)
            tokens   = tokenize(expanded)
            interp   = NovaInterpreter(tokens, self._emit)
            interp.vars = self._vars
            interp.run_all()
            self._vars = interp.vars
        except Exception as e:
            self._emit(f"[Error] {e}")

    def _run_file(self):
        path = filedialog.askopenfilename(
            title="Open .nova file",
            filetypes=[("Nova files","*.nova"),("All","*.*")])
        if not path: return
        self._emit(f"\n── Running {os.path.basename(path)} ──")
        try:
            with open(path, encoding="utf-8") as f: src = f.read()
            tokens = tokenize(preprocess(src))
            interp = NovaInterpreter(tokens, self._emit)
            interp.vars = dict(self._vars)
            interp.run_all()
        except Exception as e:
            self._emit(f"[Error] {e}")
        self._emit("── Done ──\n")

    def _clear(self):       self.terminal.delete("1.0", tk.END)
    def _reset_vars(self):  self._vars = {}; self._emit("[vars cleared]")

    def _hist_up(self, _=None):
        if self._hidx > 0:
            self._hidx -= 1; self.entry.delete(0, tk.END)
            self.entry.insert(0, self._history[self._hidx])

    def _hist_down(self, _=None):
        if self._hidx < len(self._history) - 1:
            self._hidx += 1; self.entry.delete(0, tk.END)
            self.entry.insert(0, self._history[self._hidx])
        else:
            self._hidx = len(self._history); self.entry.delete(0, tk.END)


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f: src = f.read()
        run_nova(src)
    else:
        root = tk.Tk()
        NovaInterpreterApp(root)
        root.mainloop()
