from __future__ import annotations

import io
import re
import sys
import time

TOKEN = re.compile(r'''
    (?P<ws>\s+)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<num>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
  | (?P<punct>[(){}\[\],:])
  | (?P<ident>[A-Za-z_][A-Za-z_0-9]*)
''', re.X)


def log(msg):
    sys.stderr.write('%.3f  ron %s\n' % (time.time(), msg))


BS = chr(92)
ESCAPES = {'"': '"', BS: BS, 'n': chr(10), 't': chr(9), 'r': chr(13),
           "'": "'", '0': chr(0)}


def unescape(raw):
    if BS not in raw:
        return raw
    out, i, n = [], 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch != BS or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = raw[i + 1]
        if nxt == 'u' and i + 2 < n and raw[i + 2] == '{':
            end = raw.index('}', i + 3)
            out.append(chr(int(raw[i + 3:end], 16)))
            i = end + 1
            continue
        out.append(ESCAPES.get(nxt, nxt))
        i += 2
    return ''.join(out)


def tokenize(text):
    out, pos, n = [], 0, len(text)
    while pos < n:
        m = TOKEN.match(text, pos)
        if m is None:
            raise ValueError('bad token at %d: %r' % (pos, text[pos:pos + 40]))
        pos = m.end()
        if m.lastgroup == 'ws':
            continue
        out.append((m.lastgroup, m.group()))
    return out


class Parser:

    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, value):
        kind, text = self.take()
        if text != value:
            raise ValueError('expected %r, got %r at %d' % (value, text, self.i))

    def value(self):
        kind, text = self.peek()
        if kind == 'str':
            self.take()
            return unescape(text[1:-1])
        if kind == 'num':
            self.take()
            return float(text) if ('.' in text or 'e' in text or 'E' in text) else int(text)
        if text == '[':
            return self.seq('[', ']')
        if text == '{':
            return self.mapping()
        if text == '(':
            return self.struct()
        if kind == 'ident':
            self.take()
            if text == 'true':
                return True
            if text == 'false':
                return False
            if text == 'None':
                return None
            nxt = self.peek()[1]
            if nxt == '(':
                inner = self.seq('(', ')')
                if text == 'Some':
                    return inner[0] if inner else None
                return {'_enum': text, '_args': inner}
            return text
        raise ValueError('unexpected token %r' % (text,))

    def seq(self, open_tok, close_tok):
        self.expect(open_tok)
        out = []
        while self.peek()[1] != close_tok:
            out.append(self.value())
            if self.peek()[1] == ',':
                self.take()
        self.expect(close_tok)
        return out

    def mapping(self):
        self.expect('{')
        out = {}
        while self.peek()[1] != '}':
            key = self.value()
            self.expect(':')
            out[key] = self.value()
            if self.peek()[1] == ',':
                self.take()
        self.expect('}')
        return out

    def struct(self):
        self.expect('(')
        out = {}
        while self.peek()[1] != ')':
            save = self.i
            kind, text = self.take()
            if self.peek()[1] != ':':
                self.i = save
                out.setdefault('_positional', []).append(self.value())
                if self.peek()[1] == ',':
                    self.take()
                continue
            self.take()
            out[text] = self.value()
            if self.peek()[1] == ',':
                self.take()
        self.expect(')')
        return out


def load(path):
    t0 = time.time()
    log('load enter %s' % path)
    text = io.open(path, encoding='utf-8').read()
    tokens = tokenize(text)
    log('tokenize %d tokens %.0f ms' % (len(tokens), (time.time() - t0) * 1000))
    root = Parser(tokens).value()
    defs = root['definitions']
    out = {}
    for name, versions in defs.items():
        by_version = {}
        for v in versions:
            by_version[int(v['version'])] = v['fields']
            by_version[('loc', int(v['version']))] = v.get('localised_fields') or []
        out[name] = by_version
    log('load exit %.0f ms  %d definitions  format version %s'
        % ((time.time() - t0) * 1000, len(out), root.get('version')))
    return out, root.get('version')


if __name__ == '__main__':
    defs, ver = load(sys.argv[1] if len(sys.argv) > 1
                     else r'D:\twdata\reference\ui3_extraction\schema_wh3.ron')
    total = sum(len(v) for v in defs.values())
    log('%d definitions, %d versions' % (len(defs), total))
    sample = defs['character_skills_tables']
    v = max(sample)
    log('character_skills_tables v%d fields: %s'
        % (v, [(f['name'], f['field_type'], f['is_key']) for f in sample[v]][:4]))
