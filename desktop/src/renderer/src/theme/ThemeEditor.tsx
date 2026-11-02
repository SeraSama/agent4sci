import { AlertTriangle, Check, Copy, Download, Monitor, Moon, Palette, Sun, Trash2, Upload } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge, Button, Card, ColorField, Field, Input, Segmented, Select, SectionTitle } from '@/components/ui'
import { Markdown } from '@/components/Markdown'
import { cn, toast, uid } from '@/lib/utils'
import { useTheme } from './store'
import { BUILTIN_THEMES, DEFAULT_MONO, DEFAULT_SANS, isValidTheme, runContrastChecks, TOKEN_GROUPS, type Theme,
  type ThemeTokens } from './themes'

const TOKEN_LABELS: Record<keyof ThemeTokens, string> = {
  bg: '页面背景', surface: '面板', surface2: '次级面板', elevated: '浮层', fg: '正文', fgMuted: '次要文字', fgSubtle: '弱化文字',
  border: '边框', primary: '主色', primaryFg: '主色上的文字', accent: '强调色', success: '成功', warning: '警告', danger: '危险',
  info: '信息', codeBg: '代码背景', selection: '选中', chart1: '图表 1', chart2: '图表 2', chart3: '图表 3', chart4: '图表 4',
  chart5: '图表 5', chart6: '图表 6', nodeAgent: 'Agent 节点', nodeHuman: '人工节点', nodeLoop: '循环节点', nodeTool: '工具节点',
  nodeCondition: '条件节点', nodeCheck: '检查节点',
}

const FONT_PRESETS = [
  { label: '系统无衬线', value: DEFAULT_SANS },
  { label: '衬线（宋体）', value: '"Source Han Serif SC", "Songti SC", "Noto Serif CJK SC", Georgia, serif' },
  { label: '圆体', value: '"Yuanti SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif' },
  { label: '等宽', value: DEFAULT_MONO },
]

function ThemeSwatch({ theme, active, onClick }: { theme: Theme; active: boolean; onClick: () => void }) {
  const { t } = useTranslation()
  const tk = theme.tokens
  return (
    <button onClick={onClick} className={cn('group flex flex-col gap-2 rounded-lg border p-2 text-left transition',
      active ? 'border-primary ring-2 ring-primary/25' : 'border-border hover:border-subtle')}>
      <div className="flex h-16 overflow-hidden rounded-md border" style={{ background: tk.bg, borderColor: tk.border }}>
        <div className="w-1/4" style={{ background: tk.surface, borderRight: `1px solid ${tk.border}` }} />
        <div className="flex flex-1 flex-col justify-center gap-1.5 px-2">
          <div className="h-1.5 w-3/4 rounded" style={{ background: tk.fg }} />
          <div className="h-1.5 w-1/2 rounded" style={{ background: tk.fgMuted }} />
          <div className="flex gap-1">
            <div className="h-3 w-8 rounded" style={{ background: tk.primary }} />
            <div className="h-3 w-3 rounded-full" style={{ background: tk.accent }} />
            <div className="h-3 w-3 rounded-full" style={{ background: tk.danger }} />
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between gap-1 px-0.5 text-xs">
        <span className="truncate font-medium">{t(theme.name)}</span>
        {theme.mode === 'dark' ? <Moon className="h-3 w-3 text-subtle" /> : <Sun className="h-3 w-3 text-subtle" />}
      </div>
    </button>
  )
}

export function ThemeSettings() {
  const { t } = useTranslation()
  const store = useTheme()
  const themes = store.all()
  const lightThemes = themes.filter((th) => th.mode === 'light')
  const darkThemes = themes.filter((th) => th.mode === 'dark')
  const activeId = store.active().id
  const [editing, setEditing] = useState<Theme | null>(null)

  const choose = (id: string) => void store.setSettings({ mode: 'fixed', fixed: id })

  return (
    <div className="flex flex-col gap-6">
      <section>
        <SectionTitle>{t('界面字号')}</SectionTitle>
        <div className="flex flex-wrap items-center gap-3">
          <Segmented
            value={String(store.settings.fontSize ?? 0)}
            onChange={(value) => void store.setSettings({ fontSize: Number(value) || null })}
            options={[
              { value: '0', label: t('跟随主题') },
              { value: '12', label: t('小') },
              { value: '14', label: t('默认') },
              { value: '16', label: t('大') },
              { value: '18', label: t('特大') },
              { value: '20', label: t('最大') },
            ]}
          />
          <span className="text-sm text-muted">{t('示例：Agent 正在检索文献 · 12 篇')}</span>
        </div>
      </section>
      <section>
        <SectionTitle>{t('外观模式')}</SectionTitle>
        <div className="flex flex-wrap items-end gap-4">
          <Segmented
            value={store.settings.mode}
            onChange={(mode) => void store.setSettings({ mode })}
            options={[
              { value: 'system', label: <span className="flex items-center gap-1.5"><Monitor className="h-3.5 w-3.5" />{t('跟随系统')}</span> },
              { value: 'fixed', label: <span className="flex items-center gap-1.5"><Palette className="h-3.5 w-3.5" />{t('固定主题')}</span> },
            ]}
          />
          {store.settings.mode === 'system' && (
            <>
              <Field label={t('系统为浅色时')}>
                <Select value={store.settings.light} onChange={(e) => void store.setSettings({ light: e.target.value })} className="w-40">
                  {lightThemes.map((th) => <option key={th.id} value={th.id}>{t(th.name)}</option>)}
                </Select>
              </Field>
              <Field label={t('系统为深色时')}>
                <Select value={store.settings.dark} onChange={(e) => void store.setSettings({ dark: e.target.value })} className="w-40">
                  {darkThemes.map((th) => <option key={th.id} value={th.id}>{t(th.name)}</option>)}
                </Select>
              </Field>
            </>
          )}
        </div>
      </section>

      <section>
        <SectionTitle actions={<Button size="sm" variant="primary" onClick={() => setEditing({
          ...structuredClone(store.active()), id: uid('custom-'), name: `${t(store.active().name)} ${t('（自定义）')}`, builtin: false,
        })}><Palette className="h-3.5 w-3.5" />{t('基于当前主题新建')}</Button>}>{t('主题')}</SectionTitle>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
          {themes.map((th) => (
            <div key={th.id} className="relative">
              <ThemeSwatch theme={th} active={th.id === activeId} onClick={() => choose(th.id)} />
              {!th.builtin && (
                <div className="absolute top-3 right-3 flex gap-1 opacity-0 transition group-hover:opacity-100 hover:opacity-100">
                  <button className="rounded bg-elevated/90 p-1 text-muted shadow hover:text-fg" onClick={() => setEditing(structuredClone(th))}>
                    <Palette className="h-3 w-3" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
        <ImportTheme />
      </section>

      {editing && <ThemeEditor initial={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

function ImportTheme() {
  const { t } = useTranslation()
  const input = useRef<HTMLInputElement>(null)
  const saveCustom = useTheme((s) => s.saveCustom)
  return (
    <div className="mt-3 flex gap-2">
      <input ref={input} type="file" accept="application/json,.json" className="hidden" onChange={async (e) => {
        const file = e.target.files?.[0]
        if (!file) return
        try {
          const data = JSON.parse(await file.text())
          if (!isValidTheme(data)) throw new Error(t('不是有效的主题文件'))
          await saveCustom({ ...data, id: data.id.startsWith('custom-') ? data.id : uid('custom-'), builtin: false })
          toast.success(t('已导入主题：{{name}}', { name: data.name }))
        } catch (err) {
          toast.error(String(err instanceof Error ? err.message : err))
        }
        e.target.value = ''
      }} />
      <Button size="sm" variant="outline" onClick={() => input.current?.click()}><Upload className="h-3.5 w-3.5" />{t('导入主题 JSON')}</Button>
    </div>
  )
}

function ThemeEditor({ initial, onClose }: { initial: Theme; onClose: () => void }) {
  const { t } = useTranslation()
  const { saveCustom, deleteCustom, setPreview, setSettings } = useTheme()
  const [theme, setTheme] = useState<Theme>(initial)
  const checks = useMemo(() => runContrastChecks(theme), [theme])
  const failing = checks.filter((c) => !c.ok).length

  useEffect(() => {
    setPreview(theme)
  }, [theme, setPreview])
  useEffect(() => () => setPreview(null), [setPreview])

  const setToken = (key: keyof ThemeTokens, value: string) => setTheme((th) => ({ ...th, tokens: { ...th.tokens, [key]: value } }))
  const exists = useTheme((s) => s.custom.some((c) => c.id === initial.id))

  const save = async () => {
    await saveCustom({ ...theme, builtin: false })
    await setSettings({ mode: 'fixed', fixed: theme.id })
    toast.success(t('主题已保存'))
    onClose()
  }
  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ ...theme, builtin: undefined }, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${theme.name || 'theme'}.a4s-theme.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-border bg-surface-2 px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm font-semibold"><Palette className="h-4 w-4 text-primary" />{t('主题编辑器')}
          <span className="text-xs font-normal text-muted">{t('修改会实时预览到整个应用')}</span></div>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={exportJson}><Download className="h-3.5 w-3.5" />{t('导出')}</Button>
          {exists && <Button size="sm" variant="ghost" onClick={async () => { await deleteCustom(theme.id); onClose() }}>
            <Trash2 className="h-3.5 w-3.5" />{t('删除')}</Button>}
          <Button size="sm" variant="outline" onClick={onClose}>{t('取消')}</Button>
          <Button size="sm" variant="primary" onClick={save}><Check className="h-3.5 w-3.5" />{t('保存并使用')}</Button>
        </div>
      </div>
      <div className="grid gap-6 p-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-5">
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <Field label={t('名称')}><Input value={theme.name} onChange={(e) => setTheme({ ...theme, name: e.target.value })} /></Field>
            <Field label={t('明暗')}>
              <Segmented value={theme.mode} onChange={(mode) => setTheme({ ...theme, mode })}
                options={[{ value: 'light', label: t('浅色') }, { value: 'dark', label: t('深色') }]} size="sm" />
            </Field>
            <Field label={t('圆角 {{n}}px', { n: theme.radius })}>
              <input type="range" min={0} max={16} value={theme.radius} onChange={(e) => setTheme({ ...theme, radius: Number(e.target.value) })} className="accent-[var(--a4s-primary)]" />
            </Field>
            <Field label={t('字号 {{n}}px', { n: theme.fontSize })}>
              <input type="range" min={12} max={18} value={theme.fontSize} onChange={(e) => setTheme({ ...theme, fontSize: Number(e.target.value) })} className="accent-[var(--a4s-primary)]" />
            </Field>
            <Field label={t('界面字体')} className="col-span-2">
              <Select value={FONT_PRESETS.some((f) => f.value === theme.fontSans) ? theme.fontSans : '__custom'}
                onChange={(e) => e.target.value !== '__custom' && setTheme({ ...theme, fontSans: e.target.value })}>
                {FONT_PRESETS.map((f) => <option key={f.label} value={f.value}>{t(f.label)}</option>)}
                <option value="__custom">{t('自定义…')}</option>
              </Select>
            </Field>
            <Field label={t('自定义字体栈（CSS font-family）')} className="col-span-2">
              <Input value={theme.fontSans} onChange={(e) => setTheme({ ...theme, fontSans: e.target.value })} className="font-mono text-xs" />
            </Field>
            <Field label={t('密度')} className="col-span-2">
              <Segmented value={theme.density} onChange={(density) => setTheme({ ...theme, density })} size="sm"
                options={[{ value: 'compact', label: t('紧凑') }, { value: 'comfortable', label: t('适中') }, { value: 'spacious', label: t('宽松') }]} />
            </Field>
          </div>
          {TOKEN_GROUPS.map((group) => (
            <div key={group.label}>
              <div className="mb-2 text-xs font-medium text-muted">{t(groupLabel(group.label))}</div>
              <div className="grid grid-cols-2 gap-2 xl:grid-cols-3">
                {group.keys.map((key) => (
                  <ColorField key={key} label={t(TOKEN_LABELS[key])} value={theme.tokens[key]} onChange={(v) => setToken(key, v)} />
                ))}
              </div>
            </div>
          ))}
          <div className="flex gap-2">
            {BUILTIN_THEMES.map((b) => (
              <Button key={b.id} size="sm" variant="ghost" onClick={() => setTheme({ ...theme, tokens: { ...b.tokens }, mode: b.mode })}>
                <Copy className="h-3 w-3" />{t('从「{{name}}」复制颜色', { name: t(b.name) })}
              </Button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-4">
          <div>
            <div className="mb-2 flex items-center justify-between text-xs font-medium text-muted">
              {t('对比度检查（WCAG）')}
              {failing ? <Badge tone="warning"><AlertTriangle className="h-3 w-3" />{t('{{n}} 项未达标', { n: failing })}</Badge>
                : <Badge tone="success"><Check className="h-3 w-3" />{t('全部达标')}</Badge>}
            </div>
            <div className="flex flex-col gap-1.5">
              {checks.map(({ check, ratio, ok }) => (
                <div key={check.label} className="flex items-center justify-between rounded-md border border-border px-2.5 py-1.5 text-xs">
                  <span className="flex items-center gap-2">
                    <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold" style={{ color: theme.tokens[check.fg], background: theme.tokens[check.bg] }}>Aa</span>
                    {t(checkLabel(check.label))}
                  </span>
                  <span className={cn('inline-flex items-center gap-1 font-mono', ok ? 'text-success' : 'text-warning')}>
                    {ratio.toFixed(2)} : 1
                    {ok ? <Check className="h-3 w-3" /> : <span>{`< ${check.min}`}</span>}
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-2 text-xs font-medium text-muted">{t('预览')}</div>
            <Card className="flex flex-col gap-3 p-3">
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="primary">{t('主要按钮')}</Button>
                <Button size="sm">{t('次要按钮')}</Button>
                <Button size="sm" variant="danger">{t('删除')}</Button>
              </div>
              <div className="flex flex-wrap gap-1.5">
                <Badge tone="success">{t('完成')}</Badge><Badge tone="warning">{t('等待人工')}</Badge>
                <Badge tone="danger">{t('失败')}</Badge><Badge tone="info">{t('运行中')}</Badge><Badge tone="accent">MCP</Badge>
              </div>
              <Markdown className="text-sm" text={t('**示例文本**：结果见 `results/main.json`，效应量 $d = 0.42$。') + '\n\n```python\nimport numpy as np\nx = np.arange(3)  # comment\n```'} />
              <div className="flex gap-1.5">
                {(['nodeAgent', 'nodeHuman', 'nodeLoop', 'nodeTool', 'nodeCondition', 'nodeCheck'] as const).map((k) => (
                  <div key={k} className="h-5 flex-1 rounded" style={{ background: theme.tokens[k] }} title={t(TOKEN_LABELS[k])} />
                ))}
              </div>
            </Card>
          </div>
        </div>
      </div>
    </Card>
  )
}

function groupLabel(key: string): string {
  return ({ 'theme.group.surfaces': '背景与表面', 'theme.group.text': '文字', 'theme.group.brand': '品牌色',
    'theme.group.status': '状态色', 'theme.group.charts': '图表色', 'theme.group.nodes': '节点类型色' } as Record<string, string>)[key] ?? key
}

function checkLabel(key: string): string {
  return ({ 'theme.check.bodyText': '正文 / 页面背景', 'theme.check.cardText': '正文 / 面板', 'theme.check.mutedText': '次要文字 / 面板',
    'theme.check.subtleText': '弱化文字 / 面板', 'theme.check.button': '按钮文字 / 主色', 'theme.check.danger': '危险色 / 面板',
    'theme.check.code': '代码 / 代码背景' } as Record<string, string>)[key] ?? key
}
