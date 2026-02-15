#!/usr/bin/env python3
import sys
import re

# --- LEXER ---
TOKEN_SPEC = [
    ('NUMBER',   r'\d+(\.\d+)?'),
    ('STRING',   r'"[^"]*"'),
    ('ID',       r'[A-Za-z_][A-Za-z0-9_]*'),
    ('OP',       r'==|!=|<=|>=|[+\-*/%<>=]'),
    ('LBRACE',   r'\{'), ('RBRACE',   r'\}'),
    ('LPAREN',   r'\('), ('RPAREN',   r'\)'),
    ('SEMICOLON',r';'),
    ('SKIP',     r'[ \t\n]+'),
    ('COMMENT',  r'//.*'),
    ('MISMATCH', r'.'),
]

def tokenize(code):
    tokens = []
    regex = '|'.join('(?P<%s>%s)' % pair for pair in TOKEN_SPEC)
    for mo in re.finditer(regex, code):
        kind = mo.lastgroup
        val = mo.group()
        if kind in ('SKIP', 'COMMENT'): continue
        elif kind == 'NUMBER': val = float(val) if '.' in val else int(val)
        elif kind == 'STRING': val = val[1:-1]
        elif kind == 'MISMATCH': raise SyntaxError(f'Unknown character: {val}')
        tokens.append({'type': kind, 'val': val})
    return tokens

# --- INTERPRETER ---
class NovaInterpreter:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.variables = {}

    def peek(self, offset=0):
        return self.tokens[self.pos + offset] if (self.pos + offset) < len(self.tokens) else None

    def eat(self, expected_type=None):
        token = self.peek()
        if token is None:
            raise SyntaxError("Unexpected end of input")
        if expected_type and token['type'] != expected_type:
            raise SyntaxError(f"Expected {expected_type} but got {token['type']} ({token['val']})")
        self.pos += 1
        return token

    def run_block(self):
        self.eat('LBRACE')
        while self.peek() and self.peek()['type'] != 'RBRACE':
            self.statement()
        self.eat('RBRACE')

    def statement(self):
        token = self.peek()
        if not token:
            return

        # variable declaration (have or let)
        if token['type'] == 'ID' and token['val'] in ('let', 'have'):
            self.eat('ID')
            name_tok = self.eat('ID')
            name = name_tok['val']
            # expect '=' operator
            if self.peek() and self.peek()['type'] == 'OP' and self.peek()['val'] == '=':
                self.eat('OP')
                val = self.expression()
                self.variables[name] = val
            else:
                # declaration without init
                self.variables[name] = None
            if self.peek() and self.peek()['type'] == 'SEMICOLON':
                self.eat('SEMICOLON')
            return

        # output (put or print)
        if token['type'] == 'ID' and token['val'] in ('print', 'put'):
            self.eat('ID')
            # allow both put("x") and put "x"
            if self.peek() and self.peek()['type'] == 'LPAREN':
                self.eat('LPAREN')
                val = self.expression()
                self.eat('RPAREN')
            else:
                val = self.expression()
            print(val)
            if self.peek() and self.peek()['type'] == 'SEMICOLON':
                self.eat('SEMICOLON')
            return

        # pause()
        if token['type'] == 'ID' and token['val'] == 'pause':
            self.eat('ID')
            if self.peek() and self.peek()['type'] == 'LPAREN':
                self.eat('LPAREN')
                # allow optional arg or empty
                if self.peek() and self.peek()['type'] != 'RPAREN':
                    _ = self.expression()
                self.eat('RPAREN')
            # wait for Enter
            try:
                input("Paused. Press Enter to continue...")
            except Exception:
                pass
            if self.peek() and self.peek()['type'] == 'SEMICOLON':
                self.eat('SEMICOLON')
            return

        # when (if)-otherwise
        if token['type'] == 'ID' and token['val'] == 'when':
            self.eat('ID')
            self.eat('LPAREN')
            cond = self.expression()
            self.eat('RPAREN')
            if cond:
                self.run_block()
            else:
                self.skip_block()
                # check for otherwise
                if self.peek() and self.peek()['type'] == 'ID' and self.peek()['val'] == 'otherwise':
                    self.eat('ID')
                    self.run_block()
            return

        # while
        if token['type'] == 'ID' and token['val'] == 'while':
            start_pos = self.pos
            # loop by re-evaluating condition and block
            # simple implementation: parse each loop iteration from the condition token onward
            while True:
                self.pos = start_pos
                self.eat('ID')  # while
                self.eat('LPAREN')
                cond = self.expression()
                self.eat('RPAREN')
                if not cond:
                    # skip the block so the token stream is consistent
                    self.skip_block()
                    break
                # execute block
                self.run_block()
            return

        # fallback: evaluate an expression statement
        self.expression()
        if self.peek() and self.peek()['type'] == 'SEMICOLON':
            self.eat('SEMICOLON')

    def skip_block(self):
        # skip a balanced {} block
        self.eat('LBRACE')
        depth = 1
        while depth > 0:
            t = self.eat()
            if t['type'] == 'LBRACE':
                depth += 1
            elif t['type'] == 'RBRACE':
                depth -= 1

    def expression(self):
        left = self.primary()
        while self.peek() and self.peek()['type'] == 'OP' and self.peek()['val'] in ('+', '-', '*', '/', '==', '>', '<', '='):
            op = self.eat('OP')['val']
            # treat '=' as assignment only in expression context if left is a variable name (not supporting complex lvalues)
            if op == '=':
                # left must be a variable name (we store variable names as string in primary if an ID)
                if isinstance(left, VarRef):
                    right = self.primary()
                    value = right if not isinstance(right, VarRef) else self.variables.get(right.name, 0)
                    self.variables[left.name] = value
                    left = value
                    continue
                else:
                    raise SyntaxError("Invalid assignment target")
            right = self.primary()
            if op == '+':
                if isinstance(left, str) or isinstance(right, str):
                    left = str(left) + str(right)
                else:
                    left = left + right
            elif op == '-':
                left = left - right
            elif op == '*':
                left = left * right
            elif op == '/':
                if right == 0:
                    print("Nova Error: Division by zero!")
                    sys.exit(1)
                left = left / right
            elif op == '==':
                left = (left == right)
            elif op == '>':
                left = (left > right)
            elif op == '<':
                left = (left < right)
        return left

    def primary(self):
        token = self.peek()
        if token is None:
            raise SyntaxError("Unexpected end while parsing expression")

        # Parenthesized expression
        if token['type'] == 'LPAREN':
            self.eat('LPAREN')
            val = self.expression()
            self.eat('RPAREN')
            return val

        token = self.eat()  # consume one token
        if token['type'] == 'NUMBER':
            return token['val']
        if token['type'] == 'STRING':
            return token['val']
        if token['type'] == 'ID':
            # return a VarRef object so we can support assignment like: x = 5
            return VarRef(token['val'], self.variables.get(token['val'], 0))
        # unexpected token types return 0
        return 0

class VarRef:
    def __init__(self, name, value):
        self.name = name
        self.value = value
    def __repr__(self):
        return f"<​VarRef {self.name}>"
    # allow using VarRef in arithmetic contexts by returning underlying value when used
    def __float__(self):
        try:
            return float(self.value)
        except:
            return 0.0
    def __int__(self):
        try:
            return int(self.value)
        except:
            return 0

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python nova_interpreter.py <file.nova>")
    else:
        try:
            with open(sys.argv[1], 'r', encoding='utf-8') as f:
                code = f.read()
            tokens = tokenize(code)
            interp = NovaInterpreter(tokens)
            while interp.pos < len(tokens):
                interp.statement()
        except Exception as e:
            print(f"Nova Runtime Error: {e}")