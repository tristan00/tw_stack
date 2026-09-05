import io
import re
import sys
import time

DESIGN = ['docs/db/design/03a_physical_ddl_core.md',
          'docs/db/design/03b_physical_ddl_sets_offers.md',
          'docs/db/design/03c_physical_ddl_ref_analytics.md']

CONSTRAINT_HEAD = ('PRIMARY KEY', 'UNIQUE', 'CHECK', 'FOREIGN KEY', 'EXCLUDE')
REF = re.compile(r'\s+REFERENCES\s+([\w.]+)\s*(\([^)]*\))?\s*$', re.S)
FK_ITEM = re.compile(r'FOREIGN KEY\s*\(([^)]*)\)\s+REFERENCES\s+([\w.]+)\s*(\([^)]*\))?\s*$', re.S)


def statements(src):
    out, buf, dollar = [], [], None
    for line in src.split('\n'):
        buf.append(line)
        if dollar is None:
            m = re.search(r'\$(\w*)\$', line)
            if m and line.strip().startswith('DO '):
                dollar = '$%s$' % m.group(1)
        if dollar is not None:
            if line.strip() == 'END %s;' % dollar:
                out.append('\n'.join(buf))
                buf = []
                dollar = None
            continue
        if line.rstrip().endswith(';'):
            out.append('\n'.join(buf))
            buf = []
    if [l for l in buf if l.strip()]:
        out.append('\n'.join(buf))
    return out


def top_level_split(body):
    items, depth, cur = [], 0, []
    for ch in body:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            items.append(''.join(cur))
            cur = []
            continue
        cur.append(ch)
    if ''.join(cur).strip():
        items.append(''.join(cur))
    return items


def body_bounds(stmt, after):
    open_at = stmt.index('(', after)
    depth = 0
    for i in range(open_at, len(stmt)):
        if stmt[i] == '(':
            depth += 1
        elif stmt[i] == ')':
            depth -= 1
            if depth == 0:
                return open_at, i
    raise SystemExit('unbalanced parentheses: %r' % stmt[:80])


def split_table(stmt):
    m = re.match(r'\s*CREATE TABLE\s+([\w.]+)\s*\(', stmt)
    name = m.group(1)
    short = name.split('.')[1]
    open_at, close_at = body_bounds(stmt, m.end() - 1)
    body, tail = stmt[open_at + 1:close_at], stmt[close_at + 1:]
    keep, fks, n = [], [], 0
    for item in top_level_split(body):
        s = item.strip()
        if not s:
            continue
        up = s.upper()
        if up.startswith('FOREIGN KEY'):
            n += 1
            fk = FK_ITEM.match(s)
            fks.append('ALTER TABLE %s ADD CONSTRAINT %s_fk%d FOREIGN KEY (%s) REFERENCES %s%s;'
                       % (name, short, n, fk.group(1).strip(), fk.group(2), fk.group(3) or ''))
            continue
        if any(up.startswith(h) for h in CONSTRAINT_HEAD):
            keep.append(s)
            continue
        r = REF.search(s)
        if r:
            col = s.split()[0]
            fks.append('ALTER TABLE %s ADD CONSTRAINT %s_%s_fkey FOREIGN KEY (%s) REFERENCES %s%s;'
                       % (name, short, col, col, r.group(1), r.group(2) or ''))
            s = s[:r.start()].rstrip()
        keep.append(s)
    return 'CREATE TABLE %s (\n  %s\n)%s' % (name, ',\n  '.join(keep), tail.strip()), fks


def main():
    t0 = time.time()
    sys.stderr.write('gen_sql enter\n')
    sql = []
    for path in DESIGN:
        sql += re.findall(r'```sql\n(.*?)```', io.open(path, encoding='utf-8').read(), re.S)
    tables, constraints, views, seed = [], [], [], []
    for stmt in statements('\n'.join(sql)):
        head = stmt.strip()
        if not head:
            continue
        if head.startswith('CREATE TABLE'):
            table, fks = split_table(stmt)
            tables.append(table)
            constraints += fks
        elif head.startswith('CREATE SCHEMA') or head.startswith('DO $'):
            tables.append(stmt)
        elif head.startswith('INSERT INTO dict.family'):
            tables.append(stmt)
        elif head.startswith(('CREATE INDEX', 'CREATE UNIQUE INDEX', 'ALTER TABLE')):
            constraints.append(stmt)
        elif head.startswith(('CREATE VIEW', 'CREATE OR REPLACE VIEW')):
            views.append(stmt)
        elif head.startswith('INSERT INTO'):
            seed.append(stmt)
        else:
            raise SystemExit('unclassified statement: %r' % head[:90])
    for name, part in (('03_tables', tables), ('03_constraints', constraints),
                       ('03_views', views), ('03_seed', seed)):
        io.open('sql/%s.sql' % name, 'w', encoding='utf-8', newline='\n').write('\n'.join(part).strip() + '\n')
        sys.stderr.write('%s %d statements\n' % (name, len(part)))
    sys.stderr.write('gen_sql exit %.0f ms\n' % ((time.time() - t0) * 1000))


main()
