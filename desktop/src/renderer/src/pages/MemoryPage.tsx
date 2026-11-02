import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, FolderOpen, Plus, Save, Search, ShieldAlert, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CodeEditor } from '@/components/CodeEditor'
import { confirmAction } from '@/components/confirm'
import { Badge, Button, Empty, Field, Input, PageHeader, Select, Spinner, Tip } from '@/components/ui'
import { del, errorMessage, get, put } from '@/lib/api'
import { useApp, useProjects } from '@/lib/store'
import type { Conversation, MemoryItemInfo } from '@/lib/types'
import { cn, toast } from '@/lib/utils'

interface ScopeGroup { scope: 'global' | 'project' | 'conversation'; label: string; path: string; items: MemoryItemInfo[] }
type Selection = { scope: ScopeGroup['scope']; file: string | null } | null

const TYPES: [string, string][] = [
  ['user', '用户偏好'], ['feedback', '反馈与纠正'], ['fact', '事实'], ['reference', '外部资源'], ['procedure', '做法'],
]
const SCOPE_ICON_TONE: Record<string, string> = { global: 'text-primary', project: 'text-accent', conversation: 'text-info' }

export default function MemoryPage() {
  const { t } = useTranslation()
  const { data: projects } = useProjects()
  const currentProject = useApp((s) => s.projectId)
  const [projectId, setProjectId] = useState<string>(currentProject ?? '')
  const [conversationId, setConversationId] = useState('')
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState<Selection>(null)
  const { data: convs } = useQuery({ queryKey: ['conversations'], queryFn: () => get<Conversation[]>('/conversations') })
  const scopedConvs = (convs ?? []).filter((c) => !c.temporary && c.memory_mode !== 'none' && (c.project_id ?? '') === projectId)
  const { data: groups, isLoading } = useQuery({
    queryKey: ['memory', projectId, conversationId],
    queryFn: () => get<ScopeGroup[]>('/memory', { project_id: projectId || undefined, conversation_id: conversationId || undefined }),
  })
  useEffect(() => setConversationId(''), [projectId])
  useEffect(() => setSelected(null), [projectId, conversationId])
  const needle = q.trim().toLowerCase()
  const filtered = useMemo(() => (groups ?? []).map((g) => ({
    ...g, items: g.items.filter((i) => !needle || `${i.name} ${i.description} ${i.type}`.toLowerCase().includes(needle)),
  })), [groups, needle])
  const total = groups?.reduce((n, g) => n + g.items.length, 0) ?? 0

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={t('记忆')}
        description={t('记忆是 Agent 写给自己的 Markdown 笔记，按作用域隔离：全局（跨项目）、项目、单个对话。系统提示只注入索引（MEMORY.md），全文按需读取；记忆是参考笔记而不是指令。')} />
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-6 py-2.5">
        <span className="text-xs text-muted">{t('项目')}</span>
        <Select className="h-7 w-auto max-w-56 text-xs" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          <option value="">{t('（不看项目记忆）')}</option>
          {projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </Select>
        <span className="ml-2 text-xs text-muted">{t('对话')}</span>
        <Select className="h-7 w-auto max-w-64 text-xs" value={conversationId} onChange={(e) => setConversationId(e.target.value)}>
          <option value="">{t('（不看对话记忆）')}</option>
          {scopedConvs.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
        </Select>
        <div className="relative ml-auto w-64">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <Input className="h-7 pl-8 text-xs" placeholder={t('搜索 {{n}} 条记忆', { n: total })} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="w-[320px] shrink-0 overflow-y-auto border-r border-border bg-surface/60 py-2">
          {isLoading && <div className="flex justify-center py-6"><Spinner /></div>}
          {filtered.map((g) => (
            <div key={g.scope} className="mb-2">
              <div className="flex items-center gap-1.5 px-3 py-1.5">
                <Brain className={cn('h-3.5 w-3.5', SCOPE_ICON_TONE[g.scope])} />
                <span className="text-xs font-semibold">{t(g.label)}</span>
                <span className="text-[11px] text-subtle">{g.items.length}</span>
                {window.a4s && (
                  <Tip content={g.path}><button className="ml-auto rounded p-0.5 text-subtle hover:text-fg" onClick={() => void window.a4s?.openPath(g.path)}>
                    <FolderOpen className="h-3.5 w-3.5" /></button></Tip>
                )}
                <Tip content={t('新建记忆')}>
                  <button className={cn('rounded p-0.5 text-subtle hover:text-fg', !window.a4s && 'ml-auto')} onClick={() => setSelected({ scope: g.scope, file: null })}>
                    <Plus className="h-3.5 w-3.5" /></button>
                </Tip>
              </div>
              {g.items.map((item) => {
                const active = selected?.scope === g.scope && selected.file === item.file
                return (
                  <button key={item.file} onClick={() => setSelected({ scope: g.scope, file: item.file })}
                    className={cn('mx-2 flex w-[calc(100%-16px)] flex-col rounded-md px-2.5 py-1.5 text-left transition', active ? 'bg-primary/12' : 'hover:bg-surface-2')}>
                    <span className="flex items-center gap-1.5">
                      <span className="min-w-0 flex-1 truncate text-[13px]">{item.name}</span>
                      {item.flagged && <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-warning" />}
                      <span className="shrink-0 text-[10px] text-subtle">{t(TYPES.find(([v]) => v === item.type)?.[1] ?? item.type)}</span>
                    </span>
                    <span className="line-clamp-2 text-[11px] text-muted">{item.description}</span>
                  </button>
                )
              })}
              {!g.items.length && <div className="px-4 py-1 text-[11px] text-subtle">{needle ? t('无匹配') : t('暂无')}</div>}
            </div>
          ))}
        </div>
        <div className="min-w-0 flex-1 overflow-y-auto">
          {selected ? (
            <Editor key={`${selected.scope}:${selected.file ?? 'new'}:${projectId}:${conversationId}`} selection={selected}
              group={groups?.find((g) => g.scope === selected.scope)} projectId={projectId} conversationId={conversationId}
              onSaved={(file) => setSelected({ scope: selected.scope, file })} onDeleted={() => setSelected(null)} />
          ) : (
            <Empty icon={<Brain className="h-9 w-9" />} title={t('选择一条记忆查看或编辑')}
              hint={t('对话的记忆模式决定 Agent 能读写哪些作用域：全局模式可用全部三层，项目模式可用项目与对话，仅本对话模式只用对话层，无记忆模式（临时对话）都不用。')} />
          )}
        </div>
      </div>
    </div>
  )
}

function Editor({ selection, group, projectId, conversationId, onSaved, onDeleted }: {
  selection: NonNullable<Selection>; group?: ScopeGroup; projectId: string; conversationId: string
  onSaved: (file: string) => void; onDeleted: () => void
}) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const ctx = { project_id: projectId || undefined, conversation_id: conversationId || undefined }
  const { data, isLoading, error } = useQuery({
    queryKey: ['memory-item', selection.scope, selection.file, projectId, conversationId],
    queryFn: () => get<MemoryItemInfo>('/memory/item', { scope: selection.scope, file: selection.file!, ...ctx }),
    enabled: !!selection.file,
  })
  const [form, setForm] = useState({ name: '', description: '', type: 'fact', body: '' })
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (data) setForm({ name: data.name, description: data.description, type: data.type || 'fact', body: data.body ?? '' })
  }, [data])
  if (selection.file && isLoading) return <div className="flex justify-center py-10"><Spinner /></div>
  if (error) return <Empty title={t('读取失败')} hint={errorMessage(error)} />
  const dirty = !data || form.name !== data.name || form.description !== data.description || form.type !== data.type || form.body !== (data.body ?? '')

  const save = async () => {
    setBusy(true)
    try {
      const item = await put<MemoryItemInfo>('/memory/item', {
        scope: selection.scope, file: selection.file ?? undefined, name: form.name.trim(), description: form.description.trim(),
        type: form.type, body: form.body, source: data?.source || 'manual', ...ctx,
      })
      await qc.invalidateQueries({ queryKey: ['memory'] })
      await qc.invalidateQueries({ queryKey: ['memory-item'] })
      toast.success(item.flagged ? t('已保存（内容被标记为可疑，请复核）') : t('已保存'))
      onSaved(item.file)
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex max-w-4xl flex-col gap-4 px-6 py-5">
      <div className="flex items-center gap-2">
        <Badge tone="info"><Brain className="h-3 w-3" />{t(group?.label ?? selection.scope)}</Badge>
        {selection.file ? <span className="font-mono text-xs text-muted">{selection.file}</span> : <span className="text-xs text-muted">{t('新建记忆')}</span>}
        <div className="ml-auto flex gap-2">
          {selection.file && (
            <Button variant="ghost" className="text-danger" onClick={async () => {
              if (!(await confirmAction({ title: t('删除这条记忆？'), danger: true }))) return
              try {
                await del('/memory/item', { scope: selection.scope, file: selection.file!, ...ctx })
                await qc.invalidateQueries({ queryKey: ['memory'] })
                onDeleted()
              } catch (err) {
                toast.error(errorMessage(err))
              }
            }}><Trash2 className="h-4 w-4" />{t('删除')}</Button>
          )}
          <Button variant="primary" disabled={!form.name.trim() || !dirty} loading={busy} onClick={save}><Save className="h-4 w-4" />{t('保存')}</Button>
        </div>
      </div>
      {data?.flagged && (
        <div className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          <div>{t('这条记忆包含类似提示注入的内容，可能来自网页、论文或技能。请确认后再保留。')}<div className="mt-0.5 text-muted">{data.flagged}</div></div>
        </div>
      )}
      <div className="grid grid-cols-[1fr_180px] gap-3">
        <Field label={t('名称')}><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
        <Field label={t('类型')}>
          <Select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
            {TYPES.map(([v, l]) => <option key={v} value={v}>{t(l)}</option>)}
            {!TYPES.some(([v]) => v === form.type) && <option value={form.type}>{form.type}</option>}
          </Select>
        </Field>
      </div>
      <Field label={t('一句话描述（出现在索引中，决定 Agent 是否会去读全文）')}>
        <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
      </Field>
      <Field label={t('内容')}>
        <CodeEditor value={form.body} onChange={(v) => setForm({ ...form, body: v })} height="360px" placeholder={t('用 Markdown 写下要记住的内容，以及为什么')} />
      </Field>
      {data && (
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-subtle">
          <span>{t('创建于')}：{data.created}</span><span>{t('更新于')}：{data.updated}</span>
          {data.source && <span>{t('来源')}：<span className="font-mono">{data.source}</span></span>}
        </div>
      )}
    </div>
  )
}
