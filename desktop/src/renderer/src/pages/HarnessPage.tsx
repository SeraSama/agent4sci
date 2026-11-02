import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeftRight, Bot, Brain, Copy, Layers, Pencil, Plug, Plus, RotateCcw, Save, ShieldCheck, Sparkles,
  Trash2, Wrench } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Area, AreaChart, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'
import { navigate, useRoute } from '@/app/router'
import { InteractionCard } from '@/components/agent'
import { CodeEditor } from '@/components/CodeEditor'
import { askForText, confirmAction } from '@/components/confirm'
import { useContextMenu } from '@/components/ContextMenu'
import { Timeline } from '@/components/Timeline'
import { Badge, Button, Card, Dialog, Empty, Field, Input, Select, SectionTitle, Spinner, Switch, TabPanel, Tabs, Tip } from '@/components/ui'
import { del, errorMessage, get, post, put } from '@/lib/api'
import { useEvents, type BackendEvent } from '@/lib/events'
import { useCurrentProject, useModels } from '@/lib/store'
import type { Conversation, HarnessPreview, HarnessProfile, Interaction, MemoryMode, RunSummary, ToolGroup } from '@/lib/types'
import { cn, costLabel, formatCost, formatTokens, toast } from '@/lib/utils'
import { MEMORY_MODES } from './ChatPage'

const PERMISSION_LABELS: Record<string, string> = {
  ask_all: '总是询问', ask_risky: '按需提问', allow_all: '全部允许', custom: '自定义',
}

const APPROVAL_CATEGORIES: [string, string][] = [
  ['fs.write', '写文件'], ['exec', '命令 / Python / 后台任务'], ['web', '网页'], ['literature', '文献检索'],
  ['memory', '记忆'], ['skills.install', '安装技能'], ['mcp', 'MCP 工具'], ['agent', '子代理'],
]
const SECTION_COLORS = ['var(--a4s-chart-1)', 'var(--a4s-chart-2)', 'var(--a4s-chart-3)', 'var(--a4s-chart-4)', 'var(--a4s-chart-5)', 'var(--a4s-chart-6)', 'var(--a4s-fg-subtle)']

export default function HarnessPage() {
  const { t } = useTranslation()
  const route = useRoute()
  const qc = useQueryClient()
  const { data: profiles } = useQuery({ queryKey: ['harnesses'], queryFn: () => get<HarnessProfile[]>('/harnesses') })
  const selected = route.param ?? 'assistant'
  const profile = profiles?.find((p) => p.id === selected)
  const [tab, setTab] = useState(route.query.get('tab') ?? 'structure')
  const [creating, setCreating] = useState(false)

  const duplicate = async (p: HarnessProfile) => {
    const { id: _id, builtin: _b, ...rest } = p
    const copy = await post<HarnessProfile>('/harnesses', { ...rest, name: `${t(p.name)}（${t('副本')}）` })
    await qc.invalidateQueries({ queryKey: ['harnesses'] })
    navigate('harness', copy.id)
  }

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex w-[220px] shrink-0 flex-col border-r border-border bg-surface/60">
        <div className="px-3 pt-4 pb-2 text-sm font-semibold">Harness</div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {profiles?.map((p) => (
            <HarnessRow key={p.id} profile={p} active={p.id === selected} onDuplicate={() => void duplicate(p)} />
          ))}
        </div>
        <div className="flex flex-col gap-1.5 border-t border-border p-2">
          <Button size="sm" variant="primary" className="w-full" onClick={() => setCreating(true)}>
            <Plus className="h-3.5 w-3.5" />{t('新建 Harness')}
          </Button>
          {profile && (
            <Button size="sm" variant="outline" className="w-full" onClick={() => void duplicate(profile)}>
              <Copy className="h-3.5 w-3.5" />{t('复制为新 Harness')}
            </Button>
          )}
        </div>
      </div>
      {creating && <NewHarnessDialog profiles={profiles ?? []} onClose={() => setCreating(false)} />}
      {profile ? (
        <div className="flex min-w-0 flex-1 flex-col">
          <Tabs value={tab} onValueChange={setTab} className="flex-1" tabs={[
            { value: 'structure', label: t('可视化结构') }, { value: 'config', label: t('详细配置') }, { value: 'inspect', label: t('实时检查器') },
          ]}>
            <TabPanel value="structure" className="px-6 py-5"><Structure key={profile.id} profile={profile} /></TabPanel>
            <TabPanel value="config" className="px-6 py-5"><ConfigForm key={profile.id} profile={profile} /></TabPanel>
            <TabPanel value="inspect" className="flex min-h-0 flex-col"><Inspector /></TabPanel>
          </Tabs>
        </div>
      ) : <div className="flex flex-1 items-center justify-center"><Spinner /></div>}
    </div>
  )
}

/** One row in the list. Right-click gives the same actions the buttons do, plus delete. */
function HarnessRow({ profile, active, onDuplicate }: { profile: HarnessProfile; active: boolean; onDuplicate: () => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()

  const rename = async () => {
    const name = await askForText({
      title: t('重命名 Harness'),
      input: { label: t('名称'), initial: t(profile.name), required: true },
    })
    if (!name) return
    await put(`/harnesses/${profile.id}`, { ...profile, name })
    await qc.invalidateQueries({ queryKey: ['harnesses'] })
  }

  const remove = async () => {
    const builtin = profile.builtin
    const ok = await confirmAction({
      title: builtin ? t('恢复「{{n}}」的默认设置？', { n: t(profile.name) }) : t('删除 Harness「{{n}}」？', { n: t(profile.name) }),
      description: builtin ? t('你对这个内置 Harness 的修改会被丢弃。') : t('已经用它跑过的对话和运行记录不受影响。'),
      danger: true,
      confirmLabel: builtin ? t('恢复默认') : t('删除'),
    })
    if (!ok) return
    await del(`/harnesses/${profile.id}`)
    await qc.invalidateQueries({ queryKey: ['harnesses'] })
    if (!builtin && active) navigate('harness')
    toast.success(builtin ? t('已恢复默认') : t('已删除'))
  }

  const menu = useContextMenu([
    { label: t('复制为新 Harness'), icon: <Copy className="h-3.5 w-3.5" />, onSelect: onDuplicate },
    { label: t('重命名'), icon: <Pencil className="h-3.5 w-3.5" />, onSelect: () => void rename() },
    { label: profile.builtin ? t('恢复默认') : t('删除'), icon: <Trash2 className="h-3.5 w-3.5" />,
      danger: true, separatorBefore: true, onSelect: () => void remove() },
  ])

  return (
    <>
      <button {...menu.trigger} onClick={() => navigate('harness', profile.id)}
        className={cn('flex w-full flex-col rounded-md px-2.5 py-1.5 text-left transition', active ? 'bg-primary/12' : 'hover:bg-surface-2')}>
        <span className="truncate text-sm">{t(profile.name)}</span>
        <span className="truncate text-[11px] text-subtle">{t(profile.description) || profile.id}</span>
      </button>
      {menu.menu}
    </>
  )
}

/** Electron does not implement window.prompt, so creating a harness needs a real dialog. */
function NewHarnessDialog({ profiles, onClose }: { profiles: HarnessProfile[]; onClose: () => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [name, setName] = useState(t('我的 Harness'))
  const [description, setDescription] = useState('')
  const [from, setFrom] = useState('')
  const [busy, setBusy] = useState(false)

  const create = async () => {
    if (!name.trim()) return
    setBusy(true)
    try {
      const base = profiles.find((p) => p.id === from)
      const body: Record<string, unknown> = { name: name.trim(), description: description.trim() }
      if (base) {
        const { id: _id, builtin: _b, name: _n, description: _d, ...rest } = base
        Object.assign(body, rest)
      }
      const created = await post<HarnessProfile>('/harnesses', body)
      await qc.invalidateQueries({ queryKey: ['harnesses'] })
      onClose()
      navigate('harness', created.id)
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} title={t('新建 Harness')}
      description={t('Harness 决定一个 agent 看到什么：提示、工具组、记忆、预算与权限。')}
      width="max-w-md"
      footer={<>
        <Button variant="ghost" onClick={onClose}>{t('取消')}</Button>
        <Button variant="primary" loading={busy} disabled={!name.trim()} onClick={() => void create()}>{t('创建')}</Button>
      </>}>
      <div className="flex flex-col gap-3">
        <Field label={t('名称')}>
          <Input autoFocus value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void create()} />
        </Field>
        <Field label={t('描述（可选）')}>
          <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t('这个 Harness 用来做什么')} />
        </Field>
        <Field label={t('从哪个 Harness 复制设置')} hint={t('选「从空白开始」会用一组默认值。')}>
          <Select value={from} onChange={(e) => setFrom(e.target.value)}>
            <option value="">{t('从空白开始')}</option>
            {profiles.map((p) => <option key={p.id} value={p.id}>{t(p.name)}</option>)}
          </Select>
        </Field>
      </div>
    </Dialog>
  )
}

function useSaveProfile(profile: HarnessProfile) {
  const qc = useQueryClient()
  const { t } = useTranslation()
  return async (patch: Partial<HarnessProfile>) => {
    try {
      await put(`/harnesses/${profile.id}`, { ...profile, ...patch })
      await qc.invalidateQueries({ queryKey: ['harnesses'] })
      await qc.invalidateQueries({ queryKey: ['harness-preview'] })
      toast.success(t('Harness 已保存'))
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }
}

function Structure({ profile }: { profile: HarnessProfile }) {
  const { t } = useTranslation()
  const project = useCurrentProject()
  const [mode, setMode] = useState<MemoryMode>(project ? profile.memory_mode : profile.memory_mode === 'project' ? 'global' : profile.memory_mode)
  const { data: groups } = useQuery({ queryKey: ['harness-groups'], queryFn: () => get<ToolGroup[]>('/harness-groups') })
  const { data: preview, isLoading, error } = useQuery({
    queryKey: ['harness-preview', profile.id, profile, project?.id, mode],
    queryFn: () => post<HarnessPreview>(`/harnesses/${profile.id}/preview`, { project_id: project?.id, memory_mode: mode }),
  })
  const save = useSaveProfile(profile)
  const [section, setSection] = useState<HarnessPreview['sections'][number] | null>(null)

  if (error) return <Empty title={t('无法生成预览')} hint={errorMessage(error)} action={<Button onClick={() => navigate('models')}>{t('去配置模型')}</Button>} />
  if (isLoading || !preview) return <div className="flex justify-center py-10"><Spinner /></div>
  const total = preview.total_tokens
  const color = (id: string) => SECTION_COLORS[Math.max(0, preview.sections.findIndex((x) => x.id === id)) % SECTION_COLORS.length]
  const sectionText = (sec: HarnessPreview['sections'][number]) => sec.id === 'tools'
    ? preview.tools.map((tl) => `${tl.name}${tl.native ? '  [native]' : ''}\n    ${tl.description}`).join('\n\n')
    : sec.text
  const toolsByGroup = (g: string) => preview.tools.filter((tl) => groups?.find((x) => x.id === g)?.tools.some((y) => y.name === tl.name))
  const toggleGroup = (g: string, on: boolean) => save({ tool_groups: on ? [...profile.tool_groups, g] : profile.tool_groups.filter((x) => x !== g) })

  return (
    <div className="@container flex max-w-6xl flex-col gap-5">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
        {project ? t('以项目「{{p}}」为环境预览', { p: project.name }) : t('未选择项目：以独立工作区预览')}
        <span>·</span>{t('记忆模式')}
        <Select className="h-7 w-auto text-xs" value={mode} onChange={(e) => setMode(e.target.value as MemoryMode)}>
          {MEMORY_MODES.filter((m) => m.value !== 'project' || project).map((m) => <option key={m.value} value={m.value}>{t(m.label)}</option>)}
        </Select>
      </div>
      <Card className="p-4">
        <div className="mb-2 flex items-center justify-between text-xs">
          <span className="font-medium">{t('每次请求的固定上下文')}</span>
          <span className="text-muted">{formatTokens(total)} / {formatTokens(preview.model.context_window)} tokens（{((total / preview.model.context_window) * 100).toFixed(1)}%）</span>
        </div>
        <div className="flex h-3 overflow-hidden rounded-full bg-surface-2">
          {preview.sections.filter((s) => s.tokens > 0).map((s) => (
            <Tip key={s.id} content={`${t(s.label)}：${formatTokens(s.tokens)}`}>
              <div style={{ width: `${(s.tokens / total) * 100}%`, background: color(s.id) }} />
            </Tip>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-subtle">{t('系统提示在会话开始时冻结（利于缓存）；技能与记忆只放索引，全文按需加载（渐进式披露）。')}</p>
      </Card>

      <div className="grid items-start gap-4 @lg:grid-cols-2 @4xl:items-center @4xl:grid-cols-[1fr_auto_220px_auto_1fr]">
        <Card className="flex flex-col gap-1.5 p-3 @lg:order-2 @4xl:order-none">
          <SectionTitle>{t('提示块')}</SectionTitle>
          {preview.sections.map((s) => (
            <button key={s.id} onClick={() => setSection({ ...s, text: sectionText(s) })} disabled={!sectionText(s)}
              className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-left text-xs transition hover:border-primary disabled:opacity-40">
              <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: color(s.id) }} />
              <span className="flex-1">{t(s.label)}</span>
              <span className="font-mono text-subtle">{s.tokens ? formatTokens(s.tokens) : '—'}</span>
            </button>
          ))}
        </Card>
        <ArrowLeftRight className="mx-auto hidden h-5 w-5 text-subtle @4xl:block" />
        <Card className="flex flex-col items-center gap-2 border-primary/40 p-4 text-center @lg:order-1 @lg:col-span-2 @4xl:order-none @4xl:col-span-1">
          <div className="rounded-xl bg-primary/12 p-3 text-primary"><Bot className="h-7 w-7" /></div>
          <div className="text-sm font-semibold">{t(preview.model.name)}</div>
          <div className="font-mono text-[11px] text-muted">{preview.model.model}</div>
          <div className="flex flex-wrap justify-center gap-1">
            <Badge>{preview.model.provider}</Badge>
            <Badge>{t('上下文 {{n}}', { n: formatTokens(preview.model.context_window) })}</Badge>
            {profile.effort && <Badge tone="info">effort {profile.effort}</Badge>}
          </div>
          <div className="text-[11px] text-subtle">{profile.max_turns > 0 ? t('最多 {{n}} 轮', { n: profile.max_turns }) : t('轮数无上限')}{profile.max_cost_usd ? ` · ${t('预算')} ${formatCost(profile.max_cost_usd)}` : ''}</div>
        </Card>
        <ArrowLeftRight className="mx-auto hidden h-5 w-5 text-subtle @4xl:block" />
        <Card className="flex flex-col gap-1.5 p-3 @lg:order-3 @4xl:order-none">
          <SectionTitle>{t('工具组（点击开关）')}</SectionTitle>
          {groups?.map((g) => {
            const on = profile.tool_groups.includes(g.id)
            const active = toolsByGroup(g.id).length
            return (
              <div key={g.id} className={cn('flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs', on ? 'border-primary/40 bg-primary/5' : 'border-border')}>
                <Wrench className={cn('h-3.5 w-3.5', on ? 'text-primary' : 'text-subtle')} />
                <span className="flex-1">{t(g.label)}</span>
                <span className="text-subtle">{g.id === 'mcp' ? preview.mcp.filter((m) => m.ok).length : on ? active || g.tools.length : g.tools.length}</span>
                <Switch checked={on} onCheckedChange={(v) => void toggleGroup(g.id, v)} />
              </div>
            )
          })}
        </Card>
      </div>

      <div className="grid gap-4 @lg:grid-cols-2 @4xl:grid-cols-4">
        <Card className="flex flex-col items-stretch p-3">
          <SectionTitle>{t('可见技能')}</SectionTitle>
          <div className="flex max-h-48 flex-col gap-1 overflow-y-auto">
            {preview.skills.map((s) => (
              <div key={s.name} className="flex items-center gap-1.5 text-xs"><Sparkles className="h-3 w-3 shrink-0 text-accent" />
                <span className="truncate">{s.name}</span>{s.trust === 'unreviewed' && <Badge tone="warning">{t('未审核')}</Badge>}</div>
            ))}
            {!preview.skills.length && <span className="text-xs text-subtle">{t('无')}</span>}
          </div>
          <Button size="sm" variant="ghost" className="mt-2 self-start" onClick={() => navigate('skills')}>{t('管理技能')}</Button>
        </Card>
        <Card className="flex flex-col items-stretch p-3">
          <SectionTitle>{t('MCP 服务器')}</SectionTitle>
          {preview.mcp.map((m) => (
            <div key={m.name} className="flex items-center gap-1.5 text-xs"><Plug className={cn('h-3 w-3', m.ok ? 'text-success' : 'text-danger')} />
              {m.name}<span className="text-subtle">{m.tools.length} {t('个工具')}</span></div>
          ))}
          {!preview.mcp.length && <span className="text-xs text-subtle">{t('未连接')}</span>}
          <Button size="sm" variant="ghost" className="mt-2 self-start" onClick={() => navigate('mcp')}>{t('管理 MCP')}</Button>
        </Card>
        <Card className="flex flex-col items-stretch p-3">
          <SectionTitle>{t('记忆作用域')}</SectionTitle>
          <div className="flex flex-wrap gap-1.5">
            {preview.memory_scopes.length ? preview.memory_scopes.map((s) => <Badge key={s} tone="info"><Brain className="h-3 w-3" />{s}</Badge>)
              : <Badge>{t('无记忆')}</Badge>}
          </div>
          <div className="mt-2 text-[11px] text-subtle">{t('Harness 默认：{{m}}（对话可单独切换）', { m: t(MEMORY_MODES.find((x) => x.value === profile.memory_mode)?.label ?? '') })}</div>
        </Card>
        <Card className="flex flex-col items-stretch p-3">
          <SectionTitle>{t('权限')}</SectionTitle>
          <div className="mb-1 text-xs">{t(PERMISSION_LABELS[profile.permission_mode] ?? profile.permission_mode)}</div>
          <div className="flex flex-col gap-1 text-xs">
            {profile.permission_mode === 'custom' && Object.entries(profile.approvals).map(([k, v]) => (
              <div key={k} className="flex items-center gap-1.5"><ShieldCheck className="h-3 w-3 text-muted" />{k}
                <Badge tone={v === 'deny' ? 'danger' : v === 'ask' ? 'warning' : 'success'}>{v}</Badge></div>
            ))}
            <div className="text-[11px] text-subtle">{t('其余：自动；危险命令始终需要人工确认')}</div>
          </div>
        </Card>
      </div>
      {section && (
        <Dialog open onOpenChange={(o) => !o && setSection(null)} title={t(section.label)} width="max-w-3xl"
          description={`${formatTokens(section.tokens)} tokens`}>
          <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap">{section.text}</pre>
        </Dialog>
      )}
    </div>
  )
}

function ConfigForm({ profile }: { profile: HarnessProfile }) {
  const { data: modes } = useQuery({
    queryKey: ['permission-modes'],
    queryFn: () => get<{ id: string; label: string; hint: string }[]>('/permission-modes'),
  })
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { data: groups } = useQuery({ queryKey: ['harness-groups'], queryFn: () => get<ToolGroup[]>('/harness-groups') })
  const { data: models } = useModels()
  const [p, setP] = useState(profile)
  useEffect(() => setP(profile), [profile])
  const save = useSaveProfile(p)
  const set = (patch: Partial<HarnessProfile>) => setP({ ...p, ...patch })
  const dirty = JSON.stringify(p) !== JSON.stringify(profile)

  return (
    <div className="flex max-w-4xl flex-col gap-5">
      <div className="flex items-center gap-2">
        <Button variant="primary" disabled={!dirty} onClick={() => save({})}><Save className="h-4 w-4" />{t('保存')}</Button>
        {profile.builtin ? (
          <Button variant="ghost" onClick={async () => {
            await del(`/harnesses/${profile.id}`)
            await qc.invalidateQueries({ queryKey: ['harnesses'] })
            toast.success(t('已恢复默认'))
          }}><RotateCcw className="h-4 w-4" />{t('恢复默认')}</Button>
        ) : (
          <Button variant="ghost" className="text-danger" onClick={async () => {
            if (!(await confirmAction({ title: t('删除这个 Harness？'), danger: true }))) return
            await del(`/harnesses/${profile.id}`)
            await qc.invalidateQueries({ queryKey: ['harnesses'] })
            navigate('harness')
          }}>{t('删除')}</Button>
        )}
        {profile.builtin && <span className="text-xs text-muted">{t('内置 Harness：修改会保存为覆盖版本，可随时恢复默认')}</span>}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('名称')}><Input value={t(p.name)} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field label={t('描述')}><Input value={t(p.description)} onChange={(e) => set({ description: e.target.value })} /></Field>
        <Field label={t('模型（空 = 按角色映射 / 默认）')}>
          <Select value={p.model_id ?? ''} onChange={(e) => set({ model_id: e.target.value || null })}>
            <option value="">{t('（默认）')}</option>{models?.models.map((m) => <option key={m.id} value={m.id}>{t(m.name)}</option>)}
          </Select>
        </Field>
        <Field label={t('推理强度')}>
          <Select value={p.effort ?? ''} onChange={(e) => set({ effort: e.target.value || null })}>
            <option value="">{t('（模型默认）')}</option>{['low', 'medium', 'high', 'xhigh', 'max'].map((v) => <option key={v}>{v}</option>)}
          </Select>
        </Field>
      </div>
      <Field label={t('角色提示（system prompt 的第一部分）')}>
        <CodeEditor value={p.role_prompt} onChange={(v) => set({ role_prompt: v })} height="220px" />
      </Field>
      <Switch checked={p.include_rules} onCheckedChange={(v) => set({ include_rules: v })} label={<span className="text-sm">{t('附加“工作与科研诚信规则”')}</span>} />
      <div>
        <SectionTitle>{t('可用工具')}</SectionTitle>
        <div className="grid gap-2 md:grid-cols-2">
          {groups?.map((g) => (
            <Card key={g.id} className={cn('p-3', p.tool_groups.includes(g.id) && 'border-primary/40')}>
              <Switch checked={p.tool_groups.includes(g.id)} onCheckedChange={(v) => set({ tool_groups: v ? [...p.tool_groups, g.id] : p.tool_groups.filter((x) => x !== g.id) })}
                label={<span className="text-sm font-medium">{t(g.label)}</span>} />
              <div className="mt-2 flex flex-wrap gap-1">
                {g.tools.map((tl) => {
                  const excluded = p.tool_exclude.includes(tl.name)
                  return (
                    <Tip key={tl.name} content={tl.description}>
                      <button onClick={() => set({ tool_exclude: excluded ? p.tool_exclude.filter((x) => x !== tl.name) : [...p.tool_exclude, tl.name] })}
                        className={cn('rounded border px-1.5 py-0.5 font-mono text-[10px]', excluded ? 'border-border text-subtle line-through' : 'border-primary/30 text-fg')}>
                        {tl.name}
                      </button>
                    </Tip>
                  )
                })}
                {g.id === 'mcp' && <span className="text-[11px] text-subtle">{t('来自已连接的 MCP 服务器')}</span>}
              </div>
            </Card>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('可见技能')}>
          <Select value={p.skills_mode} onChange={(e) => set({ skills_mode: e.target.value as HarnessProfile['skills_mode'] })}>
            <option value="all">{t('全部（多时按领域标签过滤）')}</option><option value="tags">{t('按标签')}</option>
            <option value="list">{t('指定列表')}</option><option value="none">{t('不使用技能')}</option>
          </Select>
        </Field>
        <Field label={t('技能标签 / 名称（逗号分隔）')}>
          <Input defaultValue={[...p.skill_tags, ...p.skill_names].join(', ')} onBlur={(e) => {
            const items = e.target.value.split(',').map((s) => s.trim()).filter(Boolean)
            set(p.skills_mode === 'list' ? { skill_names: items } : { skill_tags: items })
          }} />
        </Field>
        <Field label={t('默认记忆模式')}>
          <Select value={p.memory_mode} onChange={(e) => set({ memory_mode: e.target.value as MemoryMode })}>
            {MEMORY_MODES.map((m) => <option key={m.value} value={m.value}>{t(m.label)}</option>)}
          </Select>
        </Field>
        <Field label={t('MCP 服务器（逗号分隔，空 = 全部）')}>
          <Input defaultValue={(p.mcp_servers ?? []).join(', ')} onBlur={(e) => {
            const items = e.target.value.split(',').map((s) => s.trim()).filter(Boolean)
            set({ mcp_servers: items.length ? items : null })
          }} />
        </Field>
      </div>
      <div>
        <SectionTitle>{t('权限模式')}</SectionTitle>
        <div className="flex flex-col gap-1.5">
          {(modes ?? []).map((m) => (
            <label key={m.id} className={cn('flex items-start gap-2 rounded-md border px-3 py-2 text-sm transition',
              p.permission_mode === m.id ? 'border-primary bg-primary/8' : 'border-border hover:bg-surface-2')}>
              <input type="radio" className="mt-1" checked={p.permission_mode === m.id}
                onChange={() => set({ permission_mode: m.id })} />
              <span className="min-w-0 flex-1">
                <span className="font-medium">{t(m.label)}</span>
                <span className="mt-0.5 block text-xs text-muted">{t(m.hint)}</span>
              </span>
            </label>
          ))}
        </div>
      </div>
      {p.permission_mode === 'custom' && (
      <div>
        <SectionTitle>{t('审批策略')}</SectionTitle>
        <div className="grid gap-2 md:grid-cols-2">
          {APPROVAL_CATEGORIES.map(([cat, label]) => (
            <div key={cat} className="flex items-center gap-3 rounded-md border border-border px-3 py-1.5 text-sm">
              <span className="flex-1">{t(label)} <span className="font-mono text-[11px] text-subtle">{cat}</span></span>
              <Select className="h-7 w-24 text-xs" value={p.approvals[cat] ?? 'auto'} onChange={(e) => {
                const next = { ...p.approvals }
                if (e.target.value === 'auto') delete next[cat]; else next[cat] = e.target.value as 'ask' | 'deny'
                set({ approvals: next })
              }}>
                <option value="auto">{t('自动')}</option><option value="ask">{t('询问')}</option><option value="deny">{t('禁止')}</option>
              </Select>
            </div>
          ))}
        </div>
      </div>
      )}
      <SectionTitle>{t('运行上限')}</SectionTitle>
      <div className="grid grid-cols-3 gap-3">
        <Field label={t('最大轮数')} hint={t('0 = 无上限（只由预算和“停止”结束）')}>
          <Input type="number" min={0} value={p.max_turns} onChange={(e) => set({ max_turns: Number(e.target.value) })} /></Field>
        <Field label={t('每条回复的输出上限（token）')} hint={t('留空或 0 = 由模型适配器决定')}>
          <Input type="number" min={0} value={p.max_tokens ?? ''} onChange={(e) => set({ max_tokens: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
        <Field label={t('预算上限（美元，可空）')}><Input type="number" value={p.max_cost_usd ?? ''} onChange={(e) => set({ max_cost_usd: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
        <Field label={t('工具输出截断（字符）')}><Input type="number" value={p.tool_output_limit} onChange={(e) => set({ tool_output_limit: Number(e.target.value) })} /></Field>
        <Field label={t('压缩阈值（上下文占比）')}><Input type="number" step="0.05" value={p.compact_ratio} onChange={(e) => set({ compact_ratio: Number(e.target.value) })} /></Field>
        <Field label={t('单个工具超时（秒）')}><Input type="number" value={p.tool_timeout_s} onChange={(e) => set({ tool_timeout_s: Number(e.target.value) })} /></Field>
        <Field label={t('连接静默中断（秒）')} hint={t('多久没有收到任何数据（含心跳）就中断并重试；模型思考很久但连接正常不会被中断。0 = 不限')}>
          <Input type="number" min={0} value={p.stream_idle_timeout_s} onChange={(e) => set({ stream_idle_timeout_s: Number(e.target.value) })} /></Field>
        <Field label={t('子代理轮数上限')} hint={t('每个子代理最多跑几轮；0 = 不限制')}>
          <Input type="number" min={0} value={p.subagent_max_turns} onChange={(e) => set({ subagent_max_turns: Number(e.target.value) })} /></Field>
        <div className="flex items-end pb-1.5"><Switch checked={p.allow_subagents} onCheckedChange={(v) => set({ allow_subagents: v })} label={<span className="text-sm">{t('允许派生子代理')}</span>} /></div>
      </div>
    </div>
  )
}

function Inspector() {
  const { t } = useTranslation()
  const { data: convs } = useQuery({ queryKey: ['conversations'], queryFn: () => get<Conversation[]>('/conversations') })
  const { data: runs } = useQuery({ queryKey: ['runs', 'all'], queryFn: () => get<RunSummary[]>('/runs') })
  const [topic, setTopic] = useState<string>('')
  const [events, setEvents] = useState<BackendEvent[]>([])
  useEffect(() => {
    if (topic) return
    if (convs?.length) setTopic(`conv:${convs[0].id}`)
    else if (runs?.length) setTopic(`run:${runs[0].id}`)
  }, [convs, runs, topic])
  useEffect(() => {
    if (!topic) return
    setEvents([])
    get<BackendEvent[]>('/events', { topic }).then(setEvents).catch(() => undefined)
  }, [topic])
  useEvents(topic || null, (e) => {
    if (!e.type.endsWith('.delta')) setEvents((ev) => [...ev, e])
  })
  // approval queue across every conversation and run
  const { data: pending, refetch: refetchPending } = useQuery({
    queryKey: ['interactions', 'all'], queryFn: () => get<Interaction[]>('/interactions'), refetchInterval: 10_000,
  })
  useEvents('', (e) => {
    if (e.type === 'interaction.request' || e.type === 'interaction.resolved') void refetchPending()
  })
  const sourceName = (it: Interaction) => {
    const src = it.topics.find((x) => x.startsWith('conv:') || x.startsWith('run:')) ?? ''
    const conv = convs?.find((c) => `conv:${c.id}` === src)
    const run = runs?.find((r) => `run:${r.id}` === src)
    return { src, label: conv ? conv.title : run ? `${t(run.loop_name)} · ${run.id.slice(-6)}` : src }
  }

  const stats = useMemo(() => {
    let input = 0, cached = 0, output = 0, cost = 0, costUnknown = false
    const tools: Record<string, number> = {}
    const context: { turn: number; tokens: number }[] = []
    for (const e of events) {
      if (e.type === 'llm.response') {
        const u = e.data.usage ?? {}
        input += u.input_tokens ?? 0
        cached += u.cache_read_tokens ?? 0
        output += u.output_tokens ?? 0
        if (e.data.cost === null || e.data.cost === undefined) costUnknown = true  // model without pricing
        cost += e.data.cost ?? 0
        if (e.data.parent_span == null && e.data.purpose === 'turn') context.push({ turn: context.length + 1, tokens: e.data.context_tokens })
      }
      if (e.type === 'tool.call') tools[e.data.name] = (tools[e.data.name] ?? 0) + 1
      if (e.type === 'tool.result' && e.data.meta?.cost_usd) cost += e.data.meta.cost_usd
    }
    return { input, cached, output, cost, costUnknown, tools: Object.entries(tools).sort((a, b) => b[1] - a[1]), context }
  }, [events])

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-border px-5 py-2.5">
          <span className="shrink-0 text-xs whitespace-nowrap text-muted">{t('观察对象')}</span>
          <Select className="h-7 min-w-0 flex-1 text-xs xl:max-w-96 xl:flex-none" value={topic} onChange={(e) => setTopic(e.target.value)}>
            <option value="">—</option>
            <optgroup label={t('对话')}>{convs?.map((c) => <option key={c.id} value={`conv:${c.id}`}>{c.title}</option>)}</optgroup>
            <optgroup label={t('Loop 运行')}>{runs?.map((r) => <option key={r.id} value={`run:${r.id}`}>{t(r.loop_name)} · {r.id.slice(-6)}</option>)}</optgroup>
          </Select>
          <span className="hidden text-[11px] whitespace-nowrap text-subtle xl:inline">{t('实时更新；缩进表示子代理')}</span>
        </div>
        {!!pending?.length && (
          <div className="max-h-[45%] shrink-0 overflow-y-auto border-b border-border bg-warning/5 px-5 py-3">
            <SectionTitle>{t('待审批队列')} · {pending.length}</SectionTitle>
            <div className="flex flex-col gap-2">
              {pending.map((it) => {
                const { src, label } = sourceName(it)
                return (
                  <div key={it.id} className="flex flex-col gap-1">
                    <div className="flex items-center gap-2 text-[11px] text-muted">
                      <span className="truncate">{label}</span>
                      {src && src !== topic && <button className="shrink-0 text-primary hover:underline" onClick={() => setTopic(src)}>{t('查看')}</button>}
                    </div>
                    <InteractionCard item={it} onDone={() => void refetchPending()} />
                  </div>
                )
              })}
            </div>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto">{topic ? <Timeline events={events} /> : <Empty icon={<Layers className="h-8 w-8" />} title={t('选择一个对话或运行')} />}</div>
      </div>
      <div className="w-[240px] shrink-0 overflow-y-auto border-l border-border bg-surface/60 p-4">
        <SectionTitle>{t('用量')}</SectionTitle>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <Stat label={t('输入')} value={formatTokens(stats.input + stats.cached)} />
          <Stat label={t('其中缓存命中')} value={stats.input + stats.cached ? `${((stats.cached / (stats.input + stats.cached)) * 100).toFixed(0)}%` : '—'} />
          <Stat label={t('输出')} value={formatTokens(stats.output)} />
          <Stat label={stats.costUnknown ? t('成本（未设置价格）') : t('成本')} value={costLabel(stats.cost, stats.costUnknown)} />
        </div>
        {stats.context.length > 1 && (
          <div className="mt-4">
            <SectionTitle>{t('上下文增长')}</SectionTitle>
            <div className="h-28">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={stats.context} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
                  <XAxis dataKey="turn" fontSize={10} stroke="var(--a4s-fg-subtle)" />
                  <YAxis fontSize={10} stroke="var(--a4s-fg-subtle)" tickFormatter={(v) => formatTokens(v)} />
                  <ChartTooltip contentStyle={{ background: 'var(--a4s-elevated)', border: '1px solid var(--a4s-border)', fontSize: 11 }} />
                  <Area dataKey="tokens" stroke="var(--a4s-primary)" fill="var(--a4s-primary)" fillOpacity={0.15} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
        <div className="mt-4">
          <SectionTitle>{t('工具调用')}</SectionTitle>
          {stats.tools.map(([name, n]) => (
            <div key={name} className="mb-1 flex items-center gap-2 text-xs">
              <span className="w-28 truncate font-mono">{name}</span>
              <div className="h-1.5 flex-1 rounded-full bg-surface-2"><div className="h-full rounded-full bg-accent" style={{ width: `${(n / stats.tools[0][1]) * 100}%` }} /></div>
              <span className="w-5 text-right text-muted">{n}</span>
            </div>
          ))}
          {!stats.tools.length && <span className="text-xs text-subtle">—</span>}
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="rounded-md border border-border bg-surface p-2"><div className="text-[10px] text-subtle">{label}</div><div className="text-sm font-semibold tabular-nums">{value}</div></div>
}
