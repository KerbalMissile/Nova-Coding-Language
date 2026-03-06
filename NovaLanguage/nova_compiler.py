#!/usr/bin/env python3
"""
nova_compiler.py  –  Nova → IL → .exe
New features:
  button_grid(labelsArr, xsArr, ysArr, w, h, "Prefix")
      Compile-time unroll: creates one button per array element.
  on_button("Prefix", N) { ... }
      Defines the handler body for button Prefix_N.
  Multi-statement lines: separate statements with two or more spaces.
      e.g.  numA = 0  numB = 0  opCode = 0
  Inline blocks: { stmt  stmt }  on the same line.
"""

import os, re, shutil, subprocess
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox


# ── ilasm finder ─────────────────────────────────────────────────────────────

def find_ilasm_path():
    windir = os.environ.get("WINDIR", r"C:\Windows")
    base   = os.path.join(windir, "Microsoft.NET")
    for fw in ("Framework64", "Framework"):
        fwbase = os.path.join(base, fw)
        if not os.path.isdir(fwbase): continue
        for v in sorted([d for d in os.listdir(fwbase) if d.startswith("v")], reverse=True):
            p = os.path.join(fwbase, v, "ilasm.exe")
            if os.path.isfile(p): return p
    return None


def escape_il(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ── source pre-processor ──────────────────────────────────────────────────────

def _strip_comment(s):
    """Remove // comment from a line, respecting string literals."""
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
    """Split on 2+ spaces that are outside parens, brackets and quotes.
    Lines starting with 'have' or 'use' are never split (may have alignment spaces).
    """
    st = s.strip()
    if st.startswith('use '): return [st]
    # Multiple 'have' on one line: split on '  have ' boundaries
    if st.startswith('have '):
        parts = re.split(r'  +(?=have )', st)
        return [p.strip() for p in parts if p.strip()]
    parts, cur = [], ''
    depth_p = depth_b = 0
    in_str = False
    i = 0
    while i < len(s):
        c = s[i]
        if   c == '"' and not in_str: in_str = True;  cur += c
        elif c == '"' and in_str:     in_str = False; cur += c
        elif not in_str and c == '(': depth_p += 1; cur += c
        elif not in_str and c == ')': depth_p -= 1; cur += c
        elif not in_str and c == '[': depth_b += 1; cur += c
        elif not in_str and c == ']': depth_b -= 1; cur += c
        elif not in_str and depth_p == 0 and depth_b == 0 and c == ';':
            tok = cur.strip()
            if tok: parts.append(tok)
            cur = ''
        elif (not in_str and depth_p == 0 and depth_b == 0
              and c == ' ' and i+1 < len(s) and s[i+1] == ' '):
            tok = cur.strip()
            if tok: parts.append(tok)
            cur = ''
            while i < len(s) and s[i] == ' ': i += 1
            continue
        else: cur += c
        i += 1
    tok = cur.strip()
    if tok: parts.append(tok)
    return parts or [s.strip()]


def _expand_line(s, base_indent):
    """Expand a line with inline { } blocks and multi-statement double-spaces
    into a list of properly indented lines."""
    if '{' not in s and '}' not in s:
        return [' ' * base_indent + p for p in _split_stmts(s) if p.strip()]

    result = []
    cur = ''
    depth = 0
    in_str = False
    depth_p = 0
    for ch in s:
        if   ch == '"' and not in_str: in_str = True;  cur += ch
        elif ch == '"' and in_str:     in_str = False; cur += ch
        elif not in_str and ch == '(': depth_p += 1; cur += ch
        elif not in_str and ch == ')': depth_p -= 1; cur += ch
        elif not in_str and depth_p == 0 and ch == '{':
            for sub in _split_stmts(cur):
                if sub.strip(): result.append(' ' * (base_indent + depth*4) + sub.strip())
            cur = ''; depth += 1
        elif not in_str and depth_p == 0 and ch == '}':
            for sub in _split_stmts(cur):
                if sub.strip(): result.append(' ' * (base_indent + depth*4) + sub.strip())
            cur = ''; depth = max(0, depth-1)
        else: cur += ch
    for sub in _split_stmts(cur):
        if sub.strip(): result.append(' ' * base_indent + sub.strip())
    return result


def preprocess(nova_text):
    """Return list of (indent, stripped_line) ready for the compiler."""
    lines = []
    for ln in nova_text.splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith('//'): continue
        indent = len(ln) - len(ln.lstrip())
        clean  = _strip_comment(stripped)
        if not clean: continue
        for el in _expand_line(clean, indent):
            if el.strip():
                lines.append(el)
    return lines


# ── IL emitter ────────────────────────────────────────────────────────────────

class ILEmitter:
    def __init__(self, name="NovaProgram"):
        self.assembly    = name
        self.fields      = {}          # name -> "int"|"float"|"string"|"string[]"
        self.cctor       = []
        self.handlers    = {}          # name -> [il lines]   (ordered via handler_order)
        self.handler_order = []        # insertion order for handlers
        self.has_novaui  = False
        self._lbl        = 0
        self._arrays     = {}          # name -> [str val, ...] for compile-time use

    def ulabel(self, base):
        self._lbl += 1
        return f"{base}_{self._lbl}"

    def add_handler(self, name, body):
        if name not in self.handlers:
            self.handler_order.append(name)
        self.handlers[name] = body

    def get_il(self, main_lines):
        A = self.assembly
        il = [".assembly extern mscorlib {}"]
        if self.has_novaui:
            il.append(".assembly extern novaui {\n  .ver 1:0:0:0\n}")
        il += [f".assembly {A} {{}}", f".module {A}.exe\n",
               f".class public auto ansi beforefieldinit {A} extends [mscorlib]System.Object {{"]

        for n, t in self.fields.items():
            ft = ("int32"   if t=="int"   else
                  "float64" if t=="float" else
                  "string"  if t=="string" else "class [mscorlib]System.String[]")
            il.append(f"  .field public static {ft} {n}")

        il += ["\n  .method private hidebysig specialname rtspecialname static void .cctor() cil managed {",
               "    .maxstack 10"]
        for ln in self.cctor: il.append("    " + ln)
        il.append("    ret\n  }\n")

        il += ["  .method public hidebysig static void Main() cil managed {",
               "    .entrypoint", "    .maxstack 8", f"    call void {A}::StartApp()", "    ret\n  }",
               f"\n  .method public hidebysig static void StartApp() cil managed {{",
               "    .maxstack 64"]
        for ln in main_lines: il.append("    " + ln)
        il.append("    ret\n  }\n")

        for hname in self.handler_order:
            body = self.handlers[hname]
            il += [f"  .method public hidebysig static void {hname}() cil managed {{",
                   "    .maxstack 64"]
            for ln in body: il.append("    " + ln)
            il.append("    ret\n  }\n")

        il.append("}")
        return "\n".join(il)


# ── expression parser ─────────────────────────────────────────────────────────
# Full precedence-climbing parser — matches interpreter feature set:
#   float literals, float arithmetic, %, chained ops, comparisons,
#   unary minus, parentheses, variable array index, built-in functions.

_EXPR_TOK = re.compile(
    r'(?P<FLOAT>\d+\.\d+)'
    r'|(?P<INT>\d+)'
    r'|(?P<STR>"[^"]*")'
    r'|(?P<OP>==|!=|<=|>=|[+\-*/%<>])'
    r'|(?P<ID>[A-Za-z_]\w*)'
    r'|(?P<LP>\()|(?P<RP>\))'
    r'|(?P<LB>\[)|(?P<RB>\])'
    r'|(?P<CM>,)'
    r'|(?P<WS>\s+)'
)

def _etokenise(s):
    toks = []
    for m in _EXPR_TOK.finditer(s):
        k = m.lastgroup
        if k == 'WS': continue
        toks.append((k, m.group()))
    return toks

_PREC_LEVELS = [
    {'==', '!=', '<', '>', '<=', '>='},   # 0 — comparisons
    {'+', '-'},                            # 1 — additive
    {'*', '/', '%'},                       # 2 — multiplicative
]

def make_parser(emitter, read_file_paths):
    A = emitter.assembly

    def ensure_int(name):
        if name not in emitter.fields:
            emitter.fields[name] = "int"
            emitter.cctor += ['ldc.i4.0', f'stsfld int32 {A}::{name}']

    def ensure_float(name):
        if name not in emitter.fields:
            emitter.fields[name] = "float"
            emitter.cctor += ['ldc.r8 0.0', f'stsfld float64 {A}::{name}']

    def il_type(t):
        return ("int32"   if t == "int"   else
                "float64" if t == "float" else
                "string"  if t == "string" else "class [mscorlib]System.String[]")

    def to_float(t, il):
        if t == "int": il.append('conv.r8')

    def to_string(t, il):
        if t == "int":
            il += ["box [mscorlib]System.Int32",
                   "callvirt instance string [mscorlib]System.Object::ToString()"]
        elif t == "float":
            il += ["box [mscorlib]System.Double",
                   "callvirt instance string [mscorlib]System.Object::ToString()"]

    # ── recursive descent over token list ─────────────────────────────────────

    def prec(toks, pos, il, lvl):
        if lvl >= len(_PREC_LEVELS): return unary(toks, pos, il)
        ops = _PREC_LEVELS[lvl]
        lt, pos = prec(toks, pos, il, lvl + 1)
        while pos < len(toks) and toks[pos][0] == 'OP' and toks[pos][1] in ops:
            op = toks[pos][1]; pos += 1
            ril = []; rt, pos = prec(toks, pos, ril, lvl + 1)

            if op in ('==', '!=', '<', '>', '<=', '>='):
                if lt == "float" or rt == "float":
                    to_float(lt, il); il += ril; to_float(rt, il)
                else:
                    il += ril
                il += {'=='  : ['ceq'],
                       '!='  : ['ceq', 'ldc.i4.0', 'ceq'],
                       '<'   : ['clt'],
                       '>'   : ['cgt'],
                       '<='  : ['cgt', 'ldc.i4.0', 'ceq'],
                       '>='  : ['clt', 'ldc.i4.0', 'ceq']}[op]
                lt = "int"

            elif op == '+' and (lt == "string" or rt == "string"):
                to_string(lt, il); il += ril; to_string(rt, il)
                il.append('call string [mscorlib]System.String::Concat(string, string)')
                lt = "string"

            elif lt == "float" or rt == "float":
                to_float(lt, il); il += ril; to_float(rt, il)
                il.append({'+':'add', '-':'sub', '*':'mul', '/':'div', '%':'rem'}[op])
                lt = "float"

            else:  # both int
                if op == '/':
                    il.append('conv.r8'); to_float("int", ril); il += ril
                    il.append('div'); lt = "float"
                elif op == '%':
                    il += ril; il.append('rem'); lt = "int"
                else:
                    il += ril
                    il.append({'+':'add', '-':'sub', '*':'mul'}[op]); lt = "int"

        return lt, pos

    def unary(toks, pos, il):
        if pos < len(toks) and toks[pos] == ('OP', '-'):
            pos += 1; t, pos = primary(toks, pos, il)
            il.append('neg')
            return ("float" if t == "float" else "int"), pos
        return primary(toks, pos, il)

    def primary(toks, pos, il):
        if pos >= len(toks): il.append('ldc.i4.0'); return "int", pos
        k, v = toks[pos]

        if k == 'FLOAT': il.append(f'ldc.r8 {v}'); return "float", pos+1
        if k == 'INT':   il.append(f'ldc.i4 {v}'); return "int",   pos+1
        if k == 'STR':   il.append(f'ldstr "{escape_il(v[1:-1])}"'); return "string", pos+1

        if k == 'LP':
            pos += 1; t, pos = prec(toks, pos, il, 0)
            if pos < len(toks) and toks[pos][0] == 'RP': pos += 1
            return t, pos

        if k == 'ID':
            name = v; pos += 1

            # built-in call
            if pos < len(toks) and toks[pos][0] == 'LP':
                pos += 1  # consume (
                def one(out_il=il):
                    nonlocal pos
                    ail = []; at, pos = prec(toks, pos, ail, 0)
                    out_il += ail; return at
                def close():
                    nonlocal pos
                    if pos < len(toks) and toks[pos][0] == 'RP': pos += 1
                def comma():
                    nonlocal pos
                    if pos < len(toks) and toks[pos][0] == 'CM': pos += 1

                if name == 'read_file':
                    if pos < len(toks) and toks[pos][0] == 'STR':
                        path = toks[pos][1][1:-1]; pos += 1
                        read_file_paths.add(path)
                        il += [f'ldstr "{escape_il(path)}"',
                               'call string [mscorlib]System.IO.File::ReadAllText(string)']
                    close(); return "string", pos
                if name == 'len':
                    at = one(); close()
                    if at == "string[]":
                        il += ['ldlen', 'conv.i4']
                    else:
                        to_string(at, il)
                        il.append('callvirt instance int32 [mscorlib]System.String::get_Length()')
                    return "int", pos
                if name == 'int':
                    at = one(); close(); to_string(at, il)
                    il.append('call int32 [mscorlib]System.Int32::Parse(string)')
                    return "int", pos
                if name == 'float':
                    at = one(); close(); to_string(at, il)
                    il.append('call float64 [mscorlib]System.Double::Parse(string)')
                    return "float", pos
                if name == 'str':
                    at = one(); close(); to_string(at, il)
                    return "string", pos
                if name == 'abs':
                    at = one(); close()
                    if at == "float":
                        il.append('call float64 [mscorlib]System.Math::Abs(float64)')
                    else:
                        il.append('call int32 [mscorlib]System.Math::Abs(int32)')
                    return at, pos
                if name in ('max', 'min'):
                    at = one(); comma()
                    bt_il = []; bt, pos = prec(toks, pos, bt_il, 0); close()
                    if at == "float" or bt == "float":
                        to_float(at, il); il += bt_il; to_float(bt, il)
                        il.append(f'call float64 [mscorlib]System.Math::{"Max" if name=="max" else "Min"}(float64,float64)')
                        return "float", pos
                    else:
                        il += bt_il
                        il.append(f'call int32 [mscorlib]System.Math::{"Max" if name=="max" else "Min"}(int32,int32)')
                        return "int", pos
                if name == 'type':
                    ail = []; at, pos = prec(toks, pos, ail, 0); close()
                    ts = {"int":"int","float":"float","string":"string","string[]":"array"}.get(at,"string")
                    il.append(f'ldstr "{ts}"')
                    return "string", pos
                # unknown — skip args
                depth = 1
                while pos < len(toks) and depth:
                    if toks[pos][0] == 'LP': depth += 1
                    elif toks[pos][0] == 'RP': depth -= 1
                    pos += 1
                il.append('ldc.i4.0'); return "int", pos

            # array index: name[expr]
            if pos < len(toks) and toks[pos][0] == 'LB':
                pos += 1
                idx_il = []; it, pos = prec(toks, pos, idx_il, 0)
                if pos < len(toks) and toks[pos][0] == 'RB': pos += 1
                if name not in emitter.fields: emitter.fields[name] = "string[]"
                il.append(f'ldsfld class [mscorlib]System.String[] {A}::{name}')
                il += idx_il
                if it == "float": il.append('conv.i4')
                il.append('ldelem.ref')
                return "string", pos

            # plain variable
            if name not in emitter.fields:
                emitter.fields[name] = "string"
                emitter.cctor += ['ldstr ""', f'stsfld string {A}::{name}']
            t = emitter.fields[name]
            il.append(f'ldsfld {il_type(t)} {A}::{name}')
            return t, pos

        il.append('ldc.i4.0'); return "int", pos

    # ── public entry point ────────────────────────────────────────────────────

    def parse(expr_str, out):
        toks = _etokenise(expr_str.strip())
        if not toks: out.append('ldc.i4.0'); return "int"
        t, _ = prec(toks, 0, out, 0)
        return t

    return parse, ensure_int


# ── block compiler ────────────────────────────────────────────────────────────

def translate_nova_to_il(nova_text, assembly_name="NovaProgram"):
    lines           = preprocess(nova_text)
    emitter         = ILEmitter(assembly_name)
    read_file_paths = set()
    handler_counter = [0]
    parse, ensure_int = make_parser(emitter, read_file_paths)
    A = emitter.assembly

    def get_indent(line): return len(line) - len(line.lstrip())

    def store(name, t, il):
        ft = "int32" if t=="int" else "float64" if t=="float" else "string"
        il.append(f'stsfld {ft} {A}::{name}')

    def coerce_to_string(t, il):
        if t == "int":
            il += ["box [mscorlib]System.Int32",
                   "callvirt instance string [mscorlib]System.Object::ToString()"]
        elif t == "float":
            il += ["box [mscorlib]System.Double",
                   "callvirt instance string [mscorlib]System.Object::ToString()"]

    def compile_block(start, min_indent):
        il  = []
        idx = start
        while idx < len(lines):
            line   = lines[idx]
            indent = get_indent(line)
            if indent < min_indent: break
            s = line.strip().rstrip('{').rstrip()

            # ── use ──────────────────────────────────────────────────────────
            if re.match(r'^use\s+\w+', s):
                if 'novaui' in s: emitter.has_novaui = True
                idx += 1; continue

            # ── have ─────────────────────────────────────────────────────────
            m = re.match(r'^have\s+(\w+)\s*=\s*(.+)$', s)
            if m:
                name, val = m.group(1), m.group(2).strip()
                if val.startswith('['):
                    items = [x.strip().strip('"') for x in val[1:-1].split(',')]
                    emitter._arrays[name] = items
                    emitter.fields[name]  = "string[]"
                    emitter.cctor += [f'ldc.i4 {len(items)}',
                                      'newarr [mscorlib]System.String']
                    for i, it in enumerate(items):
                        emitter.cctor += ['dup', f'ldc.i4 {i}',
                                          f'ldstr "{escape_il(it)}"', 'stelem.ref']
                    emitter.cctor.append(f'stsfld class [mscorlib]System.String[] {A}::{name}')
                elif val.lstrip('-').isdigit():
                    emitter.fields[name] = "int"
                    emitter.cctor += [f'ldc.i4 {val}', f'stsfld int32 {A}::{name}']
                elif re.match(r'^-?\d+\.\d+$', val):
                    emitter.fields[name] = "float"
                    emitter.cctor += [f'ldc.r8 {val}', f'stsfld float64 {A}::{name}']
                elif val.startswith('"') and val.endswith('"'):
                    emitter.fields[name] = "string"
                    emitter.cctor += [f'ldstr "{escape_il(val[1:-1])}"',
                                      f'stsfld string {A}::{name}']
                else:
                    t = parse(val, [])
                    emitter.fields[name] = t
                    parse(val, emitter.cctor)
                    store(name, t, emitter.cctor)
                idx += 1; continue

            # ── ui_window ─────────────────────────────────────────────────────
            m = re.match(r'^ui_window\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$', s)
            if m:
                emitter.has_novaui = True
                title, w, h = m.groups()
                il += [f'ldstr "{escape_il(title)}"', f'ldc.i4 {w}', f'ldc.i4 {h}',
                       'call void [novaui]NovaUI.Engine::CreateWindow(string, int32, int32)']
                inner, idx = compile_block(idx+1, indent+1)
                il += inner + ['call void [novaui]NovaUI.Engine::Run()']
                continue

            # ── button ────────────────────────────────────────────────────────
            m = re.match(r'^button\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$', s)
            if m:
                txt, x, y = m.groups()
                hname = f"H_{handler_counter[0]}"; handler_counter[0] += 1
                il += [f'ldstr "{escape_il(txt)}"', f'ldc.i4 {x}', f'ldc.i4 {y}',
                       'ldnull', f'ldftn void {A}::{hname}()',
                       'newobj instance void [mscorlib]System.Action::.ctor(object, native int)',
                       'call void [novaui]NovaUI.Engine::AddButton(string, int32, int32, class [mscorlib]System.Action)']
                inner, idx = compile_block(idx+1, indent+1)
                emitter.add_handler(hname, inner)
                continue

            # ── button_grid(labelsArr, xsArr, ysArr, w, h, "Prefix") ──────────
            m = re.match(r'^button_grid\s*\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"([^"]+)"\s*\)$', s)
            if m:
                lv, xv, yv, bw, bh, prefix = m.groups()
                labels = emitter._arrays.get(lv, [])
                xs     = emitter._arrays.get(xv, [])
                ys     = emitter._arrays.get(yv, [])
                for i, lbl in enumerate(labels):
                    hname = f"{prefix}_{i}"
                    il += [f'ldstr "{escape_il(lbl)}"',
                           f'ldc.i4 {xs[i]}', f'ldc.i4 {ys[i]}',
                           'ldnull', f'ldftn void {A}::{hname}()',
                           'newobj instance void [mscorlib]System.Action::.ctor(object, native int)',
                           'call void [novaui]NovaUI.Engine::AddButton(string, int32, int32, class [mscorlib]System.Action)']
                    if hname not in emitter.handlers:
                        emitter.add_handler(hname, [])   # placeholder, filled by on_button
                idx += 1; continue

            # ── on_button("Prefix", N) ────────────────────────────────────────
            m = re.match(r'^on_button\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*\)$', s)
            if m:
                prefix, bidx = m.groups()
                hname = f"{prefix}_{bidx}"
                inner, idx = compile_block(idx+1, indent+1)
                emitter.add_handler(hname, inner)
                continue

            # ── named_label ───────────────────────────────────────────────────
            m = re.match(r'^named_label\s*\(\s*"([^"]+)"\s*,\s*"([^"]*)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$', s)
            if m:
                lid, txt, x, y, w, h = m.groups()
                il += [f'ldstr "{escape_il(lid)}"', f'ldstr "{escape_il(txt)}"',
                       f'ldc.i4 {x}', f'ldc.i4 {y}', f'ldc.i4 {w}', f'ldc.i4 {h}',
                       'call void [novaui]NovaUI.Engine::AddNamedLabel(string, string, int32, int32, int32, int32)']
                idx += 1; continue

            # ── set_label ─────────────────────────────────────────────────────
            m = re.match(r'^set_label\s*\(\s*"([^"]+)"\s*,\s*(.+)\s*\)$', s)
            if m:
                lid, expr = m.group(1), m.group(2).strip()
                il.append(f'ldstr "{escape_il(lid)}"')
                t = parse(expr, il)
                coerce_to_string(t, il)
                il.append('call void [novaui]NovaUI.Engine::UpdateLabel(string, string)')
                idx += 1; continue

            # ── label (3 or 5 args) ───────────────────────────────────────────
            m3 = re.match(r'^label\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$', s)
            if m3:
                txt, x, y = m3.groups()
                il += [f'ldstr "{escape_il(txt)}"', f'ldc.i4 {x}', f'ldc.i4 {y}',
                       'call void [novaui]NovaUI.Engine::AddLabel(string, int32, int32)']
                idx += 1; continue
            m5 = re.match(r'^label\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$', s)
            if m5:
                txt, x, y, w, h = m5.groups()
                il += [f'ldstr "{escape_il(txt)}"', f'ldc.i4 {x}', f'ldc.i4 {y}',
                       f'ldc.i4 {w}', f'ldc.i4 {h}',
                       'call void [novaui]NovaUI.Engine::AddLabel(string, int32, int32, int32, int32)']
                idx += 1; continue

            # ── clicked / otherwise (block openers, just recurse) ─────────────
            if s.startswith('clicked'):
                inner, idx = compile_block(idx+1, indent+1)
                il += inner; continue
            if s == 'otherwise':
                # handled inside when
                break

            # ── when (full expression condition) ─────────────────────────────
            m = re.match(r'^when\s*\((.+)\)$', s)
            if m:
                else_l = emitter.ulabel("ELSE"); end_l = emitter.ulabel("ENDIF")
                parse(m.group(1).strip(), il)
                il.append(f'brfalse {else_l}')
                body, idx = compile_block(idx+1, indent+1)
                il += body + [f'br {end_l}', f'{else_l}:']
                if idx < len(lines) and lines[idx].strip().rstrip('{').rstrip() == 'otherwise':
                    ob, idx = compile_block(idx+1, indent+1)
                    il += ob
                il.append(f'{end_l}:'); continue

            # ── while (full expression condition) ─────────────────────────────
            m = re.match(r'^while\s*\((.+)\)$', s)
            if m:
                lp = emitter.ulabel("LP"); le = emitter.ulabel("LPEND")
                il.append(f'{lp}:')
                parse(m.group(1).strip(), il)
                il.append(f'brfalse {le}')
                body, idx = compile_block(idx+1, indent+1)
                il += body + [f'br {lp}', f'{le}:']; continue

            # ── repeat N ──────────────────────────────────────────────────────
            m = re.match(r'^repeat\s+(.+)$', s)
            if m:
                ctr = emitter.ulabel("RC").replace("RC_", "_rc")
                lp  = emitter.ulabel("RPL"); le = emitter.ulabel("RPE")
                if ctr not in emitter.fields:
                    emitter.fields[ctr] = "int"
                    emitter.cctor += ['ldc.i4.0', f'stsfld int32 {A}::{ctr}']
                parse(m.group(1).strip(), il)
                il.append(f'stsfld int32 {A}::{ctr}')
                il.append(f'{lp}:')
                il += [f'ldsfld int32 {A}::{ctr}', f'brfalse {le}',
                       f'ldsfld int32 {A}::{ctr}', 'ldc.i4.1', 'sub',
                       f'stsfld int32 {A}::{ctr}']
                body, idx = compile_block(idx+1, indent+1)
                il += body + [f'br {lp}', f'{le}:']; continue

            # ── break (leave innermost loop — emitted as br to nearest loop end)
            # In IL we can't easily resolve the enclosing label here, so we use
            # a runtime trick: set all active repeat counters to 0 is complex;
            # instead we emit a leave.s placeholder comment so code still compiles.
            # For while loops, break is best expressed as setting the condition false
            # via a dedicated flag field per loop.  Simple approach: skip with note.
            if s == 'break':
                # Emit nothing harmful — the loop body will just finish naturally.
                # Full break support in IL requires scope tracking; omitted for now.
                idx += 1; continue

            # ── put(expr) ─────────────────────────────────────────────────────
            m = re.match(r'^put\s*\(\s*(.+)\s*\)$', s)
            if m:
                arg = m.group(1).strip()
                if arg in emitter.fields and emitter.fields[arg] == "string[]":
                    il += ['ldstr ", "', f'ldsfld class [mscorlib]System.String[] {A}::{arg}',
                           'call string [mscorlib]System.String::Join(string, string[])']
                else:
                    t = parse(arg, il); coerce_to_string(t, il)
                il.append('call void [mscorlib]System.Console::WriteLine(string)')
                idx += 1; continue

            # ── ui_message(expr) ──────────────────────────────────────────────
            m = re.match(r'^ui_message\s*\(\s*(.+)\s*\)$', s)
            if m:
                t = parse(m.group(1).strip(), il); coerce_to_string(t, il)
                il.append('call void [novaui]NovaUI.Engine::ShowMessage(string)')
                idx += 1; continue

            # ── icon ──────────────────────────────────────────────────────────
            m = re.match(r'^icon\s*\(\s*"([^"]+)"\s*\)$', s)
            if m:
                il += [f'ldstr "{escape_il(m.group(1))}"',
                       'call void [novaui]NovaUI.Engine::SetIcon(string)']
                idx += 1; continue

            # ── write_file / read_file ────────────────────────────────────────
            m = re.match(r'^write_file\s*\(\s*"([^"]+)"\s*,\s*(.+)\s*\)$', s)
            if m:
                il.append(f'ldstr "{escape_il(m.group(1))}"')
                parse(m.group(2).strip(), il)
                il.append('call void [mscorlib]System.IO.File::WriteAllText(string, string)')
                idx += 1; continue

            m = re.match(r'^(\w+)\s*=\s*read_file\s*\(\s*"([^"]+)"\s*\)$', s)
            if m:
                name, path = m.groups(); read_file_paths.add(path)
                if name not in emitter.fields: emitter.fields[name] = "string"
                il += [f'ldstr "{escape_il(path)}"',
                       'call string [mscorlib]System.IO.File::ReadAllText(string)',
                       f'stsfld string {A}::{name}']
                idx += 1; continue

            # ── App.Exit / pause ──────────────────────────────────────────────
            if s in ('App.Exit', 'App.Exit()', 'ExitApp', 'ExitApp()'):
                il.append('call void [novaui]NovaUI.Engine::ExitApp()')
                idx += 1; continue
            if s in ('pause', 'pause()'):
                il += ['call valuetype [mscorlib]System.ConsoleKeyInfo [mscorlib]System.Console::ReadKey()', 'pop']
                idx += 1; continue

            # ── array element assignment  NAME[expr] = expr ───────────────────
            m = re.match(r'^([A-Za-z_]\w*)\s*\[(.+?)\]\s*=\s*(.+)$', s)
            if m:
                aname, idx_expr, val_expr = m.group(1), m.group(2), m.group(3).strip()
                if aname not in emitter.fields: emitter.fields[aname] = "string[]"
                il.append(f'ldsfld class [mscorlib]System.String[] {A}::{aname}')
                it = parse(idx_expr.strip(), il)
                if it == "float": il.append('conv.i4')
                vt = parse(val_expr, il); coerce_to_string(vt, il)
                il.append('stelem.ref')
                idx += 1; continue

            # ── generic assignment  var = expr ────────────────────────────────
            m = re.match(r'^(\w+)\s*=\s*(.+)$', s)
            if m:
                name, expr = m.group(1), m.group(2).strip()
                if re.match(r'^-?\d+\.\d+$', expr):
                    if name not in emitter.fields:
                        emitter.fields[name] = "float"
                        emitter.cctor += ['ldc.r8 0.0', f'stsfld float64 {A}::{name}']
                    il += [f'ldc.r8 {expr}', f'stsfld float64 {A}::{name}']
                elif expr.lstrip('-').isdigit():
                    ensure_int(name)
                    il += [f'ldc.i4 {expr}', f'stsfld int32 {A}::{name}']
                else:
                    t = parse(expr, il)
                    if name not in emitter.fields:
                        emitter.fields[name] = t
                        if   t == "int":   emitter.cctor += ['ldc.i4.0',   f'stsfld int32   {A}::{name}']
                        elif t == "float": emitter.cctor += ['ldc.r8 0.0', f'stsfld float64 {A}::{name}']
                        else:              emitter.cctor += ['ldstr ""',    f'stsfld string  {A}::{name}']
                    store(name, t, il)
                idx += 1; continue

            idx += 1   # unrecognised – skip

        return il, idx

    main_il, _ = compile_block(0, 0)
    emitter._main_lines = main_il
    return emitter, read_file_paths


# ── compiler GUI ──────────────────────────────────────────────────────────────

class NovaCompilerApp:
    def __init__(self, root):
        self.root = root
        root.title("Nova Compiler"); root.geometry("900x720")
        self.script_dir   = os.path.dirname(os.path.abspath(__file__))
        self.src_dir      = tk.StringVar(value=os.getcwd())
        self.out_name     = tk.StringVar(value="NovaProgram")
        self.ilasm_path   = tk.StringVar(value=find_ilasm_path() or "")
        self.compile_type = tk.StringVar(value="exe")
        self.last_il      = ""
        self.BG = "#1e1e1e"; self.FG = "#ffffff"
        self.BTN = "#333333"; self.EBG = "#2d2d2d"
        root.configure(bg=self.BG)
        self._build_ui(); self._refresh()

    def _build_ui(self):
        top = tk.Frame(self.root, padx=6, pady=6, bg=self.BG); top.pack(fill="x")
        tk.Label(top, text="Source Folder:", bg=self.BG, fg=self.FG).pack(side="left")
        tk.Entry(top, textvariable=self.src_dir, width=60, bg=self.EBG, fg=self.FG,
                 insertbackground="white").pack(side="left", padx=5)
        tk.Button(top, text="Browse", command=self._browse, bg=self.BTN, fg=self.FG).pack(side="left")

        mid  = tk.Frame(self.root, padx=8, bg=self.BG); mid.pack(fill="both", expand=True)
        left = tk.Frame(mid, bg=self.BG); left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text=".nova files:", bg=self.BG, fg=self.FG).pack(anchor="w")
        self.listbox = tk.Listbox(left, bg=self.EBG, fg=self.FG, selectbackground="#444")
        self.listbox.pack(fill="both", expand=True)
        tk.Button(left, text="Refresh", command=self._refresh, bg=self.BTN, fg=self.FG).pack(fill="x", pady=6)

        right = tk.Frame(mid, width=320, padx=8, bg=self.BG); right.pack(side="right", fill="y")
        tk.Label(right, text="Compile Settings", font=("Arial",10,"bold"), bg=self.BG, fg=self.FG).pack(anchor="w")
        for txt, val in [("Standalone App (.exe)","exe"),("Library (.dll)","dll")]:
            tk.Radiobutton(right, text=txt, variable=self.compile_type, value=val,
                           bg=self.BG, fg=self.FG, selectcolor=self.BG).pack(anchor="w")
        for lbl, var in [("\nOutput Base Name:", self.out_name), ("\nilasm path (optional):", self.ilasm_path)]:
            tk.Label(right, text=lbl, bg=self.BG, fg=self.FG).pack(anchor="w")
            tk.Entry(right, textvariable=var, bg=self.EBG, fg=self.FG,
                     insertbackground="white").pack(fill="x", pady=5)
        tk.Button(right, text="Compile .nova Files", command=self._compile,
                  height=2, bg="#0ba300", fg="white").pack(fill="x", pady=10)
        tk.Button(right, text="Open Output Folder", command=self._open_out,
                  bg=self.BTN, fg=self.FG).pack(fill="x", pady=2)
        tk.Button(right, text="Show Generated IL", command=self._show_il,
                  bg=self.BTN, fg=self.FG).pack(fill="x", pady=2)
        self.log = scrolledtext.ScrolledText(self.root, height=18, bg="black", fg="#00ff00")
        self.log.pack(fill="both", padx=8, pady=8)

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.src_dir.get())
        if d: self.src_dir.set(d); self._refresh()

    def _refresh(self):
        self.listbox.delete(0, tk.END)
        try:
            for f in os.listdir(self.src_dir.get()):
                if f.lower().endswith(".nova"): self.listbox.insert(tk.END, f)
        except Exception as e: self.log.insert(tk.END, f"Error: {e}\n")

    def _show_il(self):
        if not self.last_il: return
        top = tk.Toplevel(self.root); top.title("Generated IL"); top.configure(bg=self.BG)
        txt = scrolledtext.ScrolledText(top, bg=self.BG, fg=self.FG); txt.pack(fill="both", expand=True)
        txt.insert("1.0", self.last_il)

    def _open_out(self):
        try: os.startfile(self.src_dir.get())
        except Exception as e: self.log.insert(tk.END, f"Error: {e}\n")

    def _compile(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("No file selected", "Please select a .nova file."); return
        fname = self.listbox.get(sel[0])
        with open(os.path.join(self.src_dir.get(), fname), encoding="utf-8") as f: src = f.read()
        out_base = self.out_name.get().strip() or "NovaProgram"
        emitter, read_paths = translate_nova_to_il(src, out_base)
        self.last_il = emitter.get_il(emitter._main_lines)
        out_folder = os.path.abspath(self.src_dir.get())
        il_path = os.path.join(out_folder, out_base + ".il")
        with open(il_path, "w", encoding="utf-8") as f: f.write(self.last_il)
        for p in read_paths:
            tp = os.path.join(out_folder, p)
            if not os.path.exists(tp):
                if os.path.dirname(p): os.makedirs(os.path.join(out_folder, os.path.dirname(p)), exist_ok=True)
                open(tp, "w").close()
        if emitter.has_novaui:
            dll_src = next((p for p in [os.path.join(out_folder,"novaui.dll"),
                                        os.path.join(self.script_dir,"novaui.dll"),
                                        os.path.join(os.getcwd(),"novaui.dll")] if os.path.isfile(p)), None)
            dst = os.path.join(out_folder, "novaui.dll")
            if dll_src and os.path.abspath(dll_src) != os.path.abspath(dst):
                try: shutil.copy2(dll_src, dst); self.log.insert(tk.END, f"Copied novaui.dll\n")
                except Exception as e: self.log.insert(tk.END, f"Warning: {e}\n")
            elif not dll_src:
                self.log.insert(tk.END, "Warning: novaui.dll not found.\n")
        ilasm = self.ilasm_path.get() or find_ilasm_path()
        if not ilasm: self.log.insert(tk.END, "Error: ilasm.exe not found.\n"); return
        ext      = ".exe" if self.compile_type.get() == "exe" else ".dll"
        out_file = os.path.join(out_folder, out_base + ext)
        cmd      = [ilasm, il_path, f"/{self.compile_type.get()}", f"/output={out_file}"]
        self.log.insert(tk.END, f"Running: {' '.join(cmd)}\n")
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, cwd=out_folder)
            self.log.insert(tk.END, p.stdout + p.stderr + "\n")
        except Exception as ex: self.log.insert(tk.END, f"Error: {ex}\n")
        self.log.insert(tk.END, "Done.\n"); self.log.see(tk.END)


if __name__ == "__main__":
    root = tk.Tk()
    NovaCompilerApp(root)
    root.mainloop()
