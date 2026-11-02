import { describe, expect, it } from 'vitest'
import { getPath, setPath, summarize } from '@/components/graph/meta'
import en from '@/i18n/en'
import { fileKind, KIND_COLOR } from '@/lib/filetypes'
import { coversRegion, DEFAULT_UI, mergeUi } from '@/lib/uiprefs'
import { BUILTIN_THEMES, contrastRatio, isValidTheme, runContrastChecks } from '@/theme/themes'

describe('themes', () => {
  it('computes WCAG contrast ratios', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 1)
    expect(contrastRatio('#777777', '#777777')).toBeCloseTo(1, 5)
  })

  it('ships valid built-in themes that pass their own contrast checks', () => {
    for (const theme of BUILTIN_THEMES) {
      expect(isValidTheme(theme), theme.id).toBe(true)
      const failing = runContrastChecks(theme).filter((r) => !r.ok).map((r) => `${r.check.label} ${r.ratio.toFixed(2)}`)
      expect(failing, theme.id).toEqual([])
    }
  })

  it('rejects malformed theme files', () => {
    expect(isValidTheme(null)).toBe(false)
    expect(isValidTheme({ id: 'x', name: 'x', tokens: { bg: 'red' } })).toBe(false)
  })
})

describe('graph helpers', () => {
  it('sets nested config paths and drops empty values', () => {
    const cfg = setPath({ a: 1 }, 'metric.name', 'acc')
    expect(getPath(cfg, 'metric.name')).toBe('acc')
    expect(setPath(cfg, 'metric.name', '')).toEqual({ a: 1 })
  })

  it('summarizes loop nodes through the translator', () => {
    const t = (key: string, o?: Record<string, unknown>) => key.replace(/\{\{(\w+)\}\}/g, (_, k) => String(o?.[k]))
    const text = summarize('loop', { max_iterations: 5, keep_discard: true, metric: { name: 'acc' } }, t)
    expect(text).toBe('≤ 5 轮 · 保留/丢弃 · 指标 acc')
  })
})

describe('i18n', () => {
  it('keeps interpolation placeholders in English translations', () => {
    const vars = (s: string) => [...s.matchAll(/\{\{\s*([^}]+?)\s*\}\}/g)].map((m) => m[1]).sort()
    const broken = Object.entries(en).filter(([zh, text]) => {
      const a = vars(zh)
      // literal examples such as “{{变量}}” are translated on purpose
      return a.some((v) => /^[\w.]+$/.test(v)) && JSON.stringify(a) !== JSON.stringify(vars(text))
    })
    expect(broken).toEqual([])
  })
})

describe('ui prefs', () => {
  it('fills in defaults for a partially stored background', () => {
    // An older build wrote fewer keys; a reload must not produce NaN opacity or an undefined region.
    const stored = { sidebarSide: 'right', background: { asset: 'a.png', kind: 'image' } }
    const ui = mergeUi(stored)
    expect(ui.sidebarSide).toBe('right')
    expect(ui.background.opacity).toBe(DEFAULT_UI.background.opacity)
    expect(ui.background.region).toBe('window')
    expect(ui.sound).toEqual(DEFAULT_UI.sound)
  })

  it('decides which panes a background covers', () => {
    const bg = DEFAULT_UI.background
    expect(coversRegion({ ...bg, asset: null, region: 'window' }, 'main')).toBe(false)
    expect(coversRegion({ ...bg, asset: 'a.png', region: 'window' }, 'sidebar')).toBe(true)
    expect(coversRegion({ ...bg, asset: 'a.png', region: 'main' }, 'sidebar')).toBe(false)
    expect(coversRegion({ ...bg, asset: 'a.png', region: 'main' }, 'main')).toBe(true)
  })
})

describe('file kinds', () => {
  it('colours the formats a lab actually drops into a chat', () => {
    const cases: [string, string][] = [
      ['notes.md', 'doc'], ['paper.pdf', 'pdf'], ['analysis.py', 'code'], ['fig1.png', 'image'],
      ['counts.csv', 'sheet'], ['talk.pptx', 'slides'], ['session.mp4', 'video'], ['interview.wav', 'audio'],
      ['sub-01_task-rest_bold.nii.gz', 'data'], ['raw.fif', 'data'], ['model.safetensors', 'data'],
      ['bundle.tar.gz', 'archive'], ['data.zip', 'archive'], ['Makefile', 'other'], ['', 'other'],
    ]
    for (const [name, kind] of cases) expect(fileKind(name), name).toBe(kind)
  })

  it('is case-insensitive and keeps a colour for every kind', () => {
    expect(fileKind('REPORT.PDF')).toBe('pdf')
    for (const kind of new Set(Object.values(KIND_COLOR))) expect(kind).toBeTruthy()
  })
})
