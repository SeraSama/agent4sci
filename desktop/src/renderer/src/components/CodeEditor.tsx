import { json } from '@codemirror/lang-json'
import { markdown } from '@codemirror/lang-markdown'
import { python } from '@codemirror/lang-python'
import { yaml } from '@codemirror/lang-yaml'
import { tags as t } from '@lezer/highlight'
import { createTheme } from '@uiw/codemirror-themes'
import CodeMirror, { EditorView } from '@uiw/react-codemirror'
import { useMemo } from 'react'
import { useTheme } from '@/theme/store'

const LANGS = { markdown, yaml, json, python } as const

export function CodeEditor({ value, onChange, language = 'markdown', height = '320px', readOnly = false, placeholder, wrap }: {
  value: string
  onChange?: (v: string) => void
  language?: keyof typeof LANGS
  height?: string
  readOnly?: boolean
  placeholder?: string
  /** Soft-wrap long lines (default: on for prose, off for code). */
  wrap?: boolean
}) {
  const mode = useTheme((s) => s.active().mode)
  const theme = useMemo(
    () =>
      createTheme({
        theme: mode,
        settings: {
          background: 'var(--a4s-code-bg)',
          foreground: 'var(--a4s-fg)',
          caret: 'var(--a4s-primary)',
          selection: 'var(--a4s-selection)',
          selectionMatch: 'var(--a4s-selection)',
          lineHighlight: 'transparent',
          gutterBackground: 'var(--a4s-code-bg)',
          gutterForeground: 'var(--a4s-fg-subtle)',
          gutterBorder: 'transparent',
          fontFamily: 'var(--a4s-font-mono)',
        },
        styles: [
          { tag: [t.heading], color: 'var(--a4s-primary)', fontWeight: '600' },
          { tag: [t.keyword, t.operatorKeyword], color: 'var(--a4s-chart-4)' },
          { tag: [t.string, t.special(t.string)], color: 'var(--a4s-chart-5)' },
          { tag: [t.number, t.bool, t.null], color: 'var(--a4s-chart-3)' },
          { tag: [t.comment], color: 'var(--a4s-fg-subtle)', fontStyle: 'italic' },
          { tag: [t.propertyName, t.attributeName], color: 'var(--a4s-chart-1)' },
          { tag: [t.link, t.url], color: 'var(--a4s-info)' },
          { tag: [t.emphasis], fontStyle: 'italic' },
          { tag: [t.strong], fontWeight: '700' },
        ],
      }),
    [mode],
  )
  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      height={height}
      theme={theme}
      readOnly={readOnly}
      placeholder={placeholder}
      extensions={(wrap ?? language === 'markdown') ? [LANGS[language](), EditorView.lineWrapping] : [LANGS[language]()]}
      basicSetup={{ foldGutter: false, highlightActiveLine: false, highlightActiveLineGutter: false }}
      className="overflow-hidden rounded-md border border-border text-[13px]"
    />
  )
}
