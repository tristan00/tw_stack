/**
 * Verify every colour token against WCAG, in both themes.
 *
 * This exists because the previous stylesheet's light theme overrode five of its eight
 * tokens and left green, amber and red at their dark-tuned values. Measured against the
 * card they sat on: 2.54:1, 2.52:1 and 3.35:1, against a 4.5:1 requirement -- every
 * semantic colour failing, on the theme actually in use, for as long as the file existed.
 * Nobody noticed because nobody measured; eyes adapt and a green that "looks green" can
 * be unreadable.
 *
 * So the palette is checked by a script rather than by looking, the script runs in the
 * build, and a regression fails rather than ships.
 *
 * Text roles need 4.5:1 (WCAG 1.4.3). Roles used only as marks -- a bar, a chart series,
 * a dot -- need 3:1 (WCAG 1.4.11 non-text contrast).
 *
 * Run: npm run check:contrast
 */

import { readFileSync } from 'node:fs'
import path from 'node:path'

const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const css = readFileSync(path.join(here, '..', 'src', 'index.css'), 'utf8')

/** Pull `--name: #hex;` pairs out of one CSS block. */
function tokensIn(block) {
  const out = {}
  for (const m of block.matchAll(/--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    out[m[1]] = m[2]
  }
  return out
}

function blockAfter(marker) {
  const i = css.indexOf(marker)
  if (i < 0) throw new Error(`could not find the CSS block: ${marker}`)
  const open = css.indexOf('{', i)
  let depth = 0
  for (let j = open; j < css.length; j++) {
    if (css[j] === '{') depth++
    else if (css[j] === '}') {
      depth--
      if (depth === 0) return css.slice(open, j)
    }
  }
  throw new Error(`unbalanced braces after ${marker}`)
}

function srgb(c) {
  const v = c / 255
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
}

function luminance(hex) {
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map((x) => x + x).join('')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b)
}

function ratio(a, b) {
  const la = luminance(a)
  const lb = luminance(b)
  const hi = Math.max(la, lb)
  const lo = Math.min(la, lb)
  return (hi + 0.05) / (lo + 0.05)
}

// Roles that carry words need 4.5:1; roles that only ever draw a mark need 3:1.
const TEXT_ROLES = ['fg', 'dim', 'ok', 'warn', 'bad', 'accent']
const MARK_ROLES = ['cat', 'gnn']

const themes = [
  { name: 'light', tokens: tokensIn(blockAfter(':root {')) },
  { name: 'dark', tokens: tokensIn(blockAfter(':root[data-theme="dark"]')) },
]

let failures = 0
const rows = []

for (const theme of themes) {
  const { tokens } = theme
  for (const surfaceName of ['surface', 'bg', 'raised']) {
    const surface = tokens[surfaceName]
    if (!surface) {
      console.error(`${theme.name}: missing --${surfaceName}`)
      failures++
      continue
    }
    for (const role of [...TEXT_ROLES, ...MARK_ROLES]) {
      const colour = tokens[role]
      if (!colour) {
        // A theme that defines only some of its roles is the original defect.
        console.error(`${theme.name}: --${role} is not defined in this theme`)
        failures++
        continue
      }
      const need = TEXT_ROLES.includes(role) ? 4.5 : 3.0
      const got = ratio(colour, surface)
      const ok = got >= need
      if (!ok) failures++
      rows.push(
        `${ok ? 'ok  ' : 'FAIL'} ${theme.name.padEnd(5)} --${role.padEnd(7)} on --${surfaceName.padEnd(
          8,
        )} ${got.toFixed(2)}:1 (needs ${need.toFixed(1)})`,
      )
    }
  }
}

console.log(rows.join('\n'))
console.log(
  `\n${rows.length} pairs checked across ${themes.length} themes — ${failures} failure(s)`,
)
process.exit(failures ? 1 : 0)
