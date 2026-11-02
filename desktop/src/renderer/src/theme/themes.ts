// Design tokens. Every colour, font, radius and density value in the UI comes from these,
// so switching or editing a theme restyles the whole app (including the node graph, code and charts).

export interface ThemeTokens {
  bg: string
  surface: string
  surface2: string
  elevated: string
  fg: string
  fgMuted: string
  fgSubtle: string
  border: string
  primary: string
  primaryFg: string
  accent: string
  success: string
  warning: string
  danger: string
  info: string
  codeBg: string
  selection: string
  chart1: string
  chart2: string
  chart3: string
  chart4: string
  chart5: string
  chart6: string
  nodeAgent: string
  nodeHuman: string
  nodeLoop: string
  nodeTool: string
  nodeCondition: string
  nodeCheck: string
}

export type Density = 'compact' | 'comfortable' | 'spacious'

export interface Theme {
  id: string
  name: string
  mode: 'light' | 'dark'
  tokens: ThemeTokens
  radius: number
  fontSans: string
  fontMono: string
  fontSize: number
  density: Density
  builtin?: boolean
}

export const DEFAULT_SANS =
  '"Inter", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, -apple-system, "Segoe UI", sans-serif'
export const DEFAULT_MONO = '"JetBrains Mono", "SF Mono", "Cascadia Code", Menlo, Consolas, monospace'

const base = { radius: 8, fontSans: DEFAULT_SANS, fontMono: DEFAULT_MONO, fontSize: 14, density: 'comfortable' as Density }

export const BUILTIN_THEMES: Theme[] = [
  {
    id: 'light', name: '明亮', mode: 'light', builtin: true, ...base,
    tokens: {
      bg: '#f6f6f3', surface: '#ffffff', surface2: '#efefea', elevated: '#ffffff', fg: '#1f2328', fgMuted: '#57606a',
      fgSubtle: '#8b929a', border: '#e1e1da', primary: '#3b5bdb', primaryFg: '#ffffff', accent: '#0c8f8e',
      success: '#1a7f37', warning: '#9a5b00', danger: '#cf222e', info: '#0969da', codeBg: '#f1f1ec', selection: '#dbe4ff',
      chart1: '#3b5bdb', chart2: '#0c8f8e', chart3: '#d9480f', chart4: '#9c36b5', chart5: '#2b8a3e', chart6: '#e67700',
      nodeAgent: '#3b5bdb', nodeHuman: '#d9480f', nodeLoop: '#9c36b5', nodeTool: '#0c8f8e', nodeCondition: '#c77700',
      nodeCheck: '#2b8a3e',
    },
  },
  {
    id: 'dark', name: '暗色', mode: 'dark', builtin: true, ...base,
    tokens: {
      bg: '#15171c', surface: '#1c1f26', surface2: '#232730', elevated: '#262a33', fg: '#e6e8eb', fgMuted: '#a3aab5',
      fgSubtle: '#6e7681', border: '#2f343e', primary: '#7b93fd', primaryFg: '#0b0d12', accent: '#38d9c7',
      success: '#3fb950', warning: '#d9a13a', danger: '#f85149', info: '#58a6ff', codeBg: '#1a1d23', selection: '#2b3a67',
      chart1: '#7b93fd', chart2: '#38d9c7', chart3: '#ff8a4c', chart4: '#d08cf0', chart5: '#5fd07b', chart6: '#f5b945',
      nodeAgent: '#7b93fd', nodeHuman: '#ff8a4c', nodeLoop: '#d08cf0', nodeTool: '#38d9c7', nodeCondition: '#f5b945',
      nodeCheck: '#5fd07b',
    },
  },
  {
    id: 'paper', name: '纸张', mode: 'light', builtin: true, ...base, radius: 6,
    fontSans: '"Source Han Serif SC", "Songti SC", "Noto Serif CJK SC", Georgia, serif',
    tokens: {
      bg: '#f4efe4', surface: '#fbf8f1', surface2: '#ede6d7', elevated: '#fffdf8', fg: '#2b2620', fgMuted: '#675d4f',
      fgSubtle: '#978c7b', border: '#dfd5c2', primary: '#9b4a1b', primaryFg: '#fffaf2', accent: '#2f6f62',
      success: '#3b7a36', warning: '#9a6400', danger: '#b3261e', info: '#2c5d8f', codeBg: '#efe8da', selection: '#f1d9b8',
      chart1: '#9b4a1b', chart2: '#2f6f62', chart3: '#5b5ea6', chart4: '#b5651d', chart5: '#6a7f3a', chart6: '#8e3b5b',
      nodeAgent: '#2c5d8f', nodeHuman: '#9b4a1b', nodeLoop: '#8e3b5b', nodeTool: '#2f6f62', nodeCondition: '#b5651d',
      nodeCheck: '#3b7a36',
    },
  },
  {
    id: 'graphite', name: '石墨', mode: 'dark', builtin: true, ...base, radius: 4,
    tokens: {
      bg: '#1a1a1c', surface: '#222225', surface2: '#2a2a2e', elevated: '#2e2e33', fg: '#ececec', fgMuted: '#a8a8ad',
      fgSubtle: '#76767c', border: '#37373c', primary: '#f5a524', primaryFg: '#1a1a1c', accent: '#8bd3dd',
      success: '#7ccf8a', warning: '#f5c451', danger: '#ff6b6b', info: '#8ab4f8', codeBg: '#1e1e21', selection: '#4a3b1c',
      chart1: '#f5a524', chart2: '#8bd3dd', chart3: '#ff6b6b', chart4: '#c3a6ff', chart5: '#7ccf8a', chart6: '#8ab4f8',
      nodeAgent: '#8ab4f8', nodeHuman: '#f5a524', nodeLoop: '#c3a6ff', nodeTool: '#8bd3dd', nodeCondition: '#f5c451',
      nodeCheck: '#7ccf8a',
    },
  },
  {
    id: 'ocean', name: '海洋', mode: 'dark', builtin: true, ...base, radius: 10,
    tokens: {
      bg: '#0c1a28', surface: '#112437', surface2: '#162e45', elevated: '#193450', fg: '#e2eef9', fgMuted: '#9db5cc',
      fgSubtle: '#6f8aa5', border: '#22405f', primary: '#4cc9f0', primaryFg: '#06131f', accent: '#72efdd',
      success: '#52d68a', warning: '#ffd166', danger: '#ff6b81', info: '#7fb7ff', codeBg: '#0f2132', selection: '#1f4a70',
      chart1: '#4cc9f0', chart2: '#72efdd', chart3: '#ffd166', chart4: '#b39cff', chart5: '#52d68a', chart6: '#ff9f68',
      nodeAgent: '#4cc9f0', nodeHuman: '#ff9f68', nodeLoop: '#b39cff', nodeTool: '#72efdd', nodeCondition: '#ffd166',
      nodeCheck: '#52d68a',
    },
  },
  {
    id: 'contrast', name: '高对比', mode: 'light', builtin: true, ...base, radius: 4, fontSize: 15,
    tokens: {
      bg: '#ffffff', surface: '#ffffff', surface2: '#f0f0f0', elevated: '#ffffff', fg: '#000000', fgMuted: '#1f1f1f',
      fgSubtle: '#404040', border: '#000000', primary: '#0033cc', primaryFg: '#ffffff', accent: '#006b5f',
      success: '#006400', warning: '#7a4a00', danger: '#b00000', info: '#003d99', codeBg: '#f2f2f2', selection: '#ffe066',
      chart1: '#0033cc', chart2: '#006b5f', chart3: '#b00000', chart4: '#6a00a8', chart5: '#006400', chart6: '#7a4a00',
      nodeAgent: '#0033cc', nodeHuman: '#b00000', nodeLoop: '#6a00a8', nodeTool: '#006b5f', nodeCondition: '#7a4a00',
      nodeCheck: '#006400',
    },
  },
]

export const TOKEN_GROUPS: { label: string; keys: (keyof ThemeTokens)[] }[] = [
  { label: 'theme.group.surfaces', keys: ['bg', 'surface', 'surface2', 'elevated', 'border', 'codeBg', 'selection'] },
  { label: 'theme.group.text', keys: ['fg', 'fgMuted', 'fgSubtle'] },
  { label: 'theme.group.brand', keys: ['primary', 'primaryFg', 'accent'] },
  { label: 'theme.group.status', keys: ['success', 'warning', 'danger', 'info'] },
  { label: 'theme.group.charts', keys: ['chart1', 'chart2', 'chart3', 'chart4', 'chart5', 'chart6'] },
  { label: 'theme.group.nodes', keys: ['nodeAgent', 'nodeHuman', 'nodeLoop', 'nodeTool', 'nodeCondition', 'nodeCheck'] },
]

const DENSITY: Record<Density, number> = { compact: 0.82, comfortable: 1, spacious: 1.18 }

const CSS_VARS: Record<keyof ThemeTokens, string> = {
  bg: '--a4s-bg', surface: '--a4s-surface', surface2: '--a4s-surface2', elevated: '--a4s-elevated', fg: '--a4s-fg',
  fgMuted: '--a4s-fg-muted', fgSubtle: '--a4s-fg-subtle', border: '--a4s-border', primary: '--a4s-primary',
  primaryFg: '--a4s-primary-fg', accent: '--a4s-accent', success: '--a4s-success', warning: '--a4s-warning',
  danger: '--a4s-danger', info: '--a4s-info', codeBg: '--a4s-code-bg', selection: '--a4s-selection',
  chart1: '--a4s-chart-1', chart2: '--a4s-chart-2', chart3: '--a4s-chart-3', chart4: '--a4s-chart-4',
  chart5: '--a4s-chart-5', chart6: '--a4s-chart-6', nodeAgent: '--a4s-node-agent', nodeHuman: '--a4s-node-human',
  nodeLoop: '--a4s-node-loop', nodeTool: '--a4s-node-tool', nodeCondition: '--a4s-node-condition',
  nodeCheck: '--a4s-node-check',
}

export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement): void {
  for (const [key, cssVar] of Object.entries(CSS_VARS)) {
    root.style.setProperty(cssVar, theme.tokens[key as keyof ThemeTokens])
  }
  root.style.setProperty('--a4s-radius', `${theme.radius}px`)
  root.style.setProperty('--a4s-font-sans', theme.fontSans || DEFAULT_SANS)
  root.style.setProperty('--a4s-font-mono', theme.fontMono || DEFAULT_MONO)
  root.style.setProperty('--a4s-font-size', `${theme.fontSize}px`)
  root.style.setProperty('--a4s-density', String(DENSITY[theme.density] ?? 1))
  root.dataset.theme = theme.id
  root.dataset.mode = theme.mode
  root.style.colorScheme = theme.mode
}

// ---- WCAG contrast -------------------------------------------------------------------------

function channel(value: number): number {
  const v = value / 255
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
}

export function parseColor(hex: string): [number, number, number] | null {
  const m = hex.trim().match(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i)
  if (!m) return null
  let h = m[1]
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}

export function luminance(hex: string): number {
  const rgb = parseColor(hex)
  if (!rgb) return 0
  const [r, g, b] = rgb.map(channel)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

export function contrastRatio(a: string, b: string): number {
  const la = luminance(a)
  const lb = luminance(b)
  const [hi, lo] = la > lb ? [la, lb] : [lb, la]
  return (hi + 0.05) / (lo + 0.05)
}

export interface ContrastCheck {
  label: string
  fg: keyof ThemeTokens
  bg: keyof ThemeTokens
  min: number
}

export const CONTRAST_CHECKS: ContrastCheck[] = [
  { label: 'theme.check.bodyText', fg: 'fg', bg: 'bg', min: 4.5 },
  { label: 'theme.check.cardText', fg: 'fg', bg: 'surface', min: 4.5 },
  { label: 'theme.check.mutedText', fg: 'fgMuted', bg: 'surface', min: 4.5 },
  { label: 'theme.check.subtleText', fg: 'fgSubtle', bg: 'surface', min: 3 },
  { label: 'theme.check.button', fg: 'primaryFg', bg: 'primary', min: 4.5 },
  { label: 'theme.check.danger', fg: 'danger', bg: 'surface', min: 3 },
  { label: 'theme.check.code', fg: 'fg', bg: 'codeBg', min: 4.5 },
]

export function runContrastChecks(theme: Theme): { check: ContrastCheck; ratio: number; ok: boolean }[] {
  return CONTRAST_CHECKS.map((check) => {
    const ratio = contrastRatio(theme.tokens[check.fg], theme.tokens[check.bg])
    return { check, ratio, ok: ratio >= check.min }
  })
}

export function isValidTheme(value: unknown): value is Theme {
  if (!value || typeof value !== 'object') return false
  const t = value as Theme
  if (typeof t.id !== 'string' || typeof t.name !== 'string' || !t.tokens) return false
  return (Object.keys(CSS_VARS) as (keyof ThemeTokens)[]).every((k) => parseColor(String(t.tokens[k] ?? '')) !== null)
}
