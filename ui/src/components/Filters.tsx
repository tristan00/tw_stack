import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { X } from 'lucide-react'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'


export type FilterKind = 'number' | 'enum'

export interface FilterField<T> {
  key: string
  label: string
  kind: FilterKind

  value: (row: T) => number | string | null | undefined
  unit?: string
  step?: number
}

export interface Predicate {
  field: string
  op: string
  value: string
}

export const NUM_OPS = [
  { key: 'ge', label: '≥' },
  { key: 'gt', label: '>' },
  { key: 'le', label: '≤' },
  { key: 'lt', label: '<' },
  { key: 'eq', label: '=' },
]

const ENUM_OPS = [
  { key: 'is', label: 'is' },
  { key: 'not', label: 'is not' },
]

export function opsFor(kind: FilterKind) {
  return kind === 'number' ? NUM_OPS : ENUM_OPS
}

function opLabel(kind: FilterKind, op: string) {
  return (opsFor(kind).find((o) => o.key === op) || { label: op }).label
}


function decode(raw: string | null): Predicate[] {
  if (!raw) return []
  return raw
    .split(',')
    .map((chunk) => chunk.split(':'))
    .filter((p) => p.length >= 3)
    .map((p) => ({ field: p[0], op: p[1], value: p.slice(2).join(':') }))
}

function encode(preds: Predicate[]) {
  return preds.map((p) => `${p.field}:${p.op}:${p.value}`).join(',')
}

function passes<T>(row: T, p: Predicate, field: FilterField<T>) {
  const v = field.value(row)
  if (field.kind === 'enum') {
    const s = v === null || v === undefined ? '' : String(v)
    return p.op === 'not' ? s !== p.value : s === p.value
  }

  if (v === null || v === undefined) return false
  const a = Number(v)
  const b = Number(p.value)
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false
  switch (p.op) {
    case 'ge':
      return a >= b
    case 'gt':
      return a > b
    case 'le':
      return a <= b
    case 'lt':
      return a < b
    case 'eq':
      return a === b
    default:
      return true
  }
}

export function useFilters<T>(fields: FilterField<T>[], rows: T[], param = 'f') {
  const [params, setParams] = useSearchParams()
  const preds = useMemo(() => decode(params.get(param)), [params, param])
  const byKey = useMemo(() => new Map(fields.map((f) => [f.key, f])), [fields])

  const set = (next: Predicate[]) => {
    const q = new URLSearchParams(params)
    if (next.length) q.set(param, encode(next))
    else q.delete(param)
    setParams(q, { replace: true })
  }

  const filtered = useMemo(
    () =>
      rows.filter((r) =>
        preds.every((p) => {
          const f = byKey.get(p.field)


          return f ? passes(r, p, f) : true
        }),
      ),
    [rows, preds, byKey],
  )

  return { preds, set, filtered }
}


export function optionsOf<T>(field: FilterField<T>, rows: T[]) {
  const seen = new Map<string, string>()
  for (const r of rows) {
    const v = field.value(r)
    const s = v === null || v === undefined ? '' : String(v)
    if (!seen.has(s)) seen.set(s, s)
  }
  return [...seen.keys()].sort()
}

export function FilterBar<T>({
  fields,
  rows,
  filtered,
  preds,
  set,
  noun = 'rows',
  labelFor,
}: {
  fields: FilterField<T>[]
  rows: T[]
  filtered: T[]
  preds: Predicate[]
  set: (p: Predicate[]) => void
  noun?: string

  labelFor?: (fieldKey: string, value: string) => string
}) {
  const byKey = new Map(fields.map((f) => [f.key, f]))
  const show = (p: Predicate) => {
    const f = byKey.get(p.field)
    if (!f) return `${p.field} ${p.op} ${p.value}`
    const val =
      f.kind === 'enum'
        ? labelFor?.(f.key, p.value) ?? (p.value || 'not recorded')
        : `${p.value}${f.unit ? ` ${f.unit}` : ''}`
    return `${f.label} ${opLabel(f.kind, p.op)} ${val}`
  }

  return (
    <div className="mb-3 space-y-2">
      <AddFilter fields={fields} rows={rows} preds={preds} set={set} labelFor={labelFor} />
      {preds.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {preds.map((p, i) => (
            <span
              key={`${p.field}-${p.op}-${p.value}-${i}`}
              className="bg-raised text-fg inline-flex items-center gap-1 rounded px-2 py-0.5 text-2xs"
            >
              <span className="num">{show(p)}</span>
              <button
                aria-label={`remove ${show(p)}`}
                onClick={() => set(preds.filter((_x, j) => j !== i))}
                className="text-dim hover:text-bad"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
          <button onClick={() => set([])} className="text-dim hover:text-fg text-2xs underline">
            clear all
          </button>
        </div>
      )}
      {}
      <div className="text-dim text-2xs">
        showing <span className="num text-fg">{n(filtered.length)}</span> of{' '}
        <span className="num">{n(rows.length)}</span> {noun}
        {preds.length > 0 && ` · ${preds.length} filter${preds.length > 1 ? 's' : ''} applied`}
      </div>
    </div>
  )
}

function AddFilter<T>({
  fields,
  rows,
  preds,
  set,
  labelFor,
}: {
  fields: FilterField<T>[]
  rows: T[]
  preds: Predicate[]
  set: (p: Predicate[]) => void
  labelFor?: (fieldKey: string, value: string) => string
}) {
  const [params, setParams] = useSearchParams()
  const draftKey = params.get('_d') || fields[0].key
  const field = fields.find((f) => f.key === draftKey) || fields[0]
  const ops = opsFor(field.kind)
  const draftOp = params.get('_o') || ops[0].key
  const draftVal = params.get('_v') ?? ''
  const setDraft = (k: string, v: string) => {
    const q = new URLSearchParams(params)
    if (v) q.set(k, v)
    else q.delete(k)
    if (k === '_d') q.delete('_o')
    setParams(q, { replace: true })
  }
  const commit = (value: string) => {
    if (value === '') return
    set([...preds, { field: field.key, op: draftOp, value }])
    const q = new URLSearchParams(params)
    q.delete('_v')
    setParams(q, { replace: true })
  }

  const sel =
    'bg-surface border-line text-fg rounded border px-1.5 py-1 text-2xs focus:outline-accent focus:outline-1'

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-dim text-2xs">filter</span>
      <select className={sel} value={field.key} onChange={(e) => setDraft('_d', e.target.value)}>
        {fields.map((f) => (
          <option key={f.key} value={f.key}>
            {f.label}
          </option>
        ))}
      </select>
      <select className={sel} value={draftOp} onChange={(e) => setDraft('_o', e.target.value)}>
        {ops.map((o) => (
          <option key={o.key} value={o.key}>
            {o.label}
          </option>
        ))}
      </select>
      {field.kind === 'enum' ? (
        <select
          className={cn(sel, 'min-w-40')}
          value=""
          onChange={(e) => e.target.value !== '' && commit(e.target.value)}
        >
          <option value="">choose…</option>
          {optionsOf(field, rows).map((v) => (
            <option key={v} value={v}>
              {labelFor?.(field.key, v) ?? (v || 'not recorded')}
            </option>
          ))}
        </select>
      ) : (
        <>
          <input
            className={cn(sel, 'num w-24')}
            type="number"
            step={field.step ?? 'any'}
            placeholder={field.unit ?? 'value'}
            value={draftVal}
            onChange={(e) => setDraft('_v', e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && commit(draftVal)}
          />
          <button
            onClick={() => commit(draftVal)}
            disabled={draftVal === ''}
            className="bg-raised text-fg hover:bg-raised/70 rounded px-2 py-1 text-2xs disabled:opacity-40"
          >
            add
          </button>
        </>
      )}
    </div>
  )
}
