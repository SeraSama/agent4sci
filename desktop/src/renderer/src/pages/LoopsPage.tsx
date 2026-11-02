import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Braces, CheckCircle2, Copy, Download, FileInput, Play, Plus, Save, Settings2, Trash2,
  Upload, Workflow } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { navigate, useRoute } from '@/app/router'
import { CodeEditor } from '@/components/CodeEditor'
import { confirmAction } from '@/components/confirm'
import { useContextMenu } from '@/components/ContextMenu'
import { LoopCanvas } from '@/components/graph/LoopCanvas'
import { FIELDS, getPath, NODE_META, PALETTE_ORDER, ROLES, setPath, type FieldSpec } from '@/components/graph/meta'
import { Badge, Button, Dialog, Empty, Field, Input, Select, Spinner, Switch, Textarea, Tip } from '@/components/ui'
import { api, del, errorMessage, get, post, put } from '@/lib/api'
import { useCurrentProject, useModels, useProjects } from '@/lib/store'
import type { HarnessProfile, Issue, LoopEdge, LoopGraph, LoopNode, ToolGroup } from '@/lib/types'
import { cn, toast, uid } from '@/lib/utils'
import { MEMORY_MODES } from './ChatPage'

interface LoopSummary { id: string; name: string; description: string; category: string; builtin: boolean; version: number; nodes: number }

const CATEGORY_LABEL: Record<string, string> = { pipeline: '科研流水线', pattern: '循环范式', custom: '我的 Loop' }

export default function LoopsPage() {
  const { t } = useTranslation()
  const route = useRoute()
  const qc = useQueryClient()
  const { data: loops } = useQuery({ queryKey: ['loops'], queryFn: () => get<LoopSummary[]>('/loops') })
  const selected = route.param ?? loops?.[0]?.id
  const groups = useMemo(() => {
    const out: Record<string, LoopSummary[]> = {}
    for (const l of loops ?? []) (out[l.builtin ? l.category : 'custom'] ??= []).push(l)
    return out
  }, [loops])

  const createBlank = async () => {
    const graph = {
      name: t('新 Loop'), description: '', category: 'custom', inputs: { task: { label: t('任务'), type: 'text', required: true, default: null } },
      nodes: [
        { id: 'start', type: 'start', label: '开始', position: { x: 0, y: 0 }, parent: null, config: {} },
        { id: 'agent_1', type: 'agent', label: 'Agent', position: { x: -60, y: 110 }, parent: null, config: { harness: 'assistant', prompt: '{{inputs.task}}' } },
        { id: 'end', type: 'end', label: '结束', position: { x: 0, y: 240 }, parent: null, config: {} },
      ],
      edges: [{ id: uid('e_'), source: 'start', target: 'agent_1', condition: null, label: '' }, { id: uid('e_'), source: 'agent_1', target: 'end', condition: null, label: '' }],
    }
    const saved = await post<LoopGraph>('/loops', graph)
    await qc.invalidateQueries({ queryKey: ['loops'] })
    navigate('loops', saved.id)
  }

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex w-[232px] shrink-0 flex-col border-r border-border bg-surface/60">
        <div className="flex gap-2 px-3 pt-4 pb-2">
          <Button variant="primary" size="sm" className="flex-1" onClick={createBlank}><Plus className="h-3.5 w-3.5" />{t('新建 Loop')}</Button>
          <ImportButton />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {(['pipeline', 'pattern', 'custom'] as const).filter((c) => groups[c]?.length).map((c) => (
            <div key={c} className="mt-2">
              <div className="px-2 pb-1 text-[11px] font-medium text-subtle">{t(CATEGORY_LABEL[c])}</div>
              {groups[c].map((l) => <LoopRow key={l.id} loop={l} active={l.id === selected} />)}
            </div>
          ))}
        </div>
      </div>
      {selected ? <Designer key={selected} id={selected} /> : (
        <div className="flex-1"><Empty icon={<Workflow className="h-10 w-10" />} title={t('选择或新建一个 Loop')}
          hint={t('Loop 把多个 Agent 和检查点连成固定流程：谁先做、做完谁检查、不合格就重做、什么时候停。适合要跑很久又不想盯着的任务（文献综述、实验迭代、写稿评审）。对话里的单个 Agent 用 Harness 配置（角色、工具、权限），Loop 是在外面编排多个这样的 Agent，并带上轮数、预算与保留/回滚。可以从左边的内置模板改，也可以在对话里把 Harness 切到「主理人」，说清要什么让它生成。')} /></div>
      )}
    </div>
  )
}

/** A Loop in the list, with the actions that used to need the designer open. */
function LoopRow({ loop, active }: { loop: LoopSummary; active: boolean }) {
  const { t } = useTranslation()
  const qc = useQueryClient()

  const duplicate = async () => {
    const copy = await post<LoopGraph>(`/loops/${loop.id}/duplicate`, {})
    await qc.invalidateQueries({ queryKey: ['loops'] })
    navigate('loops', copy.id)
  }

  const exportYaml = async () => {
    const yaml = await api<string>(`/loops/${loop.id}/yaml`, { text: true })
    const url = URL.createObjectURL(new Blob([yaml], { type: 'text/yaml' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${loop.id}.yaml`
    a.click()
    URL.revokeObjectURL(url)
  }

  const remove = async () => {
    const ok = await confirmAction({
      title: loop.builtin ? t('恢复「{{n}}」的内置版本？', { n: t(loop.name) }) : t('删除 Loop「{{n}}」？', { n: t(loop.name) }),
      description: t('已有的运行记录不受影响。'),
      danger: true,
      confirmLabel: loop.builtin ? t('恢复默认') : t('删除'),
    })
    if (!ok) return
    await del(`/loops/${loop.id}`)
    await qc.invalidateQueries({ queryKey: ['loops'] })
    if (active) navigate('loops')
  }

  const menu = useContextMenu([
    { label: t('复制一份'), icon: <Copy className="h-3.5 w-3.5" />, onSelect: () => void duplicate() },
    { label: t('导出 YAML'), icon: <Download className="h-3.5 w-3.5" />, onSelect: () => void exportYaml() },
    { label: loop.builtin ? t('恢复默认') : t('删除'), icon: <Trash2 className="h-3.5 w-3.5" />, danger: true,
      separatorBefore: true, onSelect: () => void remove() },
  ])

  return (
    <>
      <button {...menu.trigger} onClick={() => navigate('loops', loop.id)}
        className={cn('flex w-full flex-col rounded-md px-2.5 py-1.5 text-left transition', active ? 'bg-primary/12' : 'hover:bg-surface-2')}>
        <span className="truncate text-sm">{t(loop.name)}</span>
        <span className="truncate text-[11px] text-subtle">{loop.nodes} {t('个节点')}{loop.builtin ? '' : ` · v${loop.version}`}</span>
      </button>
      {menu.menu}
    </>
  )
}

function ImportButton() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  return (
    <>
      <input ref={input} type="file" accept=".yaml,.yml" className="hidden" onChange={async (e) => {
        const file = e.target.files?.[0]
        if (!file) return
        try {
          const g = await post<LoopGraph>('/loops/import', { yaml: await file.text() })
          await qc.invalidateQueries({ queryKey: ['loops'] })
          navigate('loops', g.id)
          toast.success(t('已导入'))
        } catch (err) {
          toast.error(errorMessage(err))
        }
        e.target.value = ''
      }} />
      <Tip content={t('导入 YAML')}><Button size="sm" variant="outline" onClick={() => input.current?.click()}><Upload className="h-3.5 w-3.5" /></Button></Tip>
    </>
  )
}

function Designer({ id }: { id: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['loop', id], queryFn: () => get<LoopGraph>(`/loops/${id}`) })
  const [graph, setGraph] = useState<LoopGraph | null>(null)
  const [dirty, setDirty] = useState(false)
  const [issues, setIssues] = useState<Issue[]>([])
  const [selection, setSelection] = useState<{ kind: 'node' | 'edge'; id: string } | null>(null)
  const [dialog, setDialog] = useState<'run' | 'settings' | 'inputs' | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (data) {
      setGraph(data)
      setIssues(data.issues ?? [])
      setDirty(false)
    }
  }, [data])

  useEffect(() => {
    if (!graph || !dirty) return
    const timer = setTimeout(() => {
      post<Issue[]>('/loops/validate', graph).then(setIssues).catch(() => undefined)
    }, 400)
    return () => clearTimeout(timer)
  }, [graph, dirty])

  if (!graph) return <div className="flex flex-1 items-center justify-center"><Spinner /></div>
  const readOnly = graph.builtin
  const change = (g: LoopGraph) => {
    if (readOnly) {
      toast.info(t('内置模板只读：点击“复制为我的 Loop”后再编辑'))
      return
    }
    setGraph(g)
    setDirty(true)
  }
  const save = async () => {
    setSaving(true)
    try {
      const saved = await put<LoopGraph>(`/loops/${graph.id}`, graph)
      setGraph({ ...graph, version: saved.version })
      setDirty(false)
      await qc.invalidateQueries({ queryKey: ['loops'] })
      toast.success(t('已保存为 v{{v}}', { v: saved.version }))
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }
  const errors = issues.filter((i) => i.level === 'error')
  const selectedNode = selection?.kind === 'node' ? graph.nodes.find((n) => n.id === selection.id) : undefined
  const selectedEdge = selection?.kind === 'edge' ? graph.edges.find((e) => e.id === selection.id) : undefined

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <input value={t(graph.name)} disabled={readOnly} onChange={(e) => change({ ...graph, name: e.target.value })}
          className="min-w-0 flex-1 truncate bg-transparent text-sm font-semibold outline-none" />
        {readOnly ? <Badge>{t('内置模板')}</Badge> : <Badge>v{graph.version}{dirty ? ' *' : ''}</Badge>}
        {errors.length ? <Tip content={errors.map((e) => t(e.message)).join(t('；'))}><Badge tone="danger"><AlertTriangle className="h-3 w-3" />{t('{{n}} 个错误', { n: errors.length })}</Badge></Tip>
          : <Badge tone="success"><CheckCircle2 className="h-3 w-3" />{t('校验通过')}</Badge>}
        <Button size="sm" variant="ghost" onClick={() => setDialog('inputs')}><Braces className="h-3.5 w-3.5" />{t('输入')}</Button>
        <Button size="sm" variant="ghost" onClick={() => setDialog('settings')}><Settings2 className="h-3.5 w-3.5" />{t('设置')}</Button>
        <Tip content={t('导出 YAML')}>
          <Button size="icon" variant="ghost" onClick={async () => {
            const text = await api<string>(`/loops/${graph.id}/yaml`, { text: true })
            const a = document.createElement('a')
            a.href = URL.createObjectURL(new Blob([text], { type: 'text/yaml' }))
            a.download = `${graph.id}.yaml`
            a.click()
          }}><Download className="h-3.5 w-3.5" /></Button>
        </Tip>
        {readOnly ? (
          <Button size="sm" onClick={async () => {
            const copy = await post<LoopGraph>(`/loops/${graph.id}/duplicate`, { name: `${t(graph.name)}（${t('我的')}）` })
            await qc.invalidateQueries({ queryKey: ['loops'] })
            navigate('loops', copy.id)
          }}><Copy className="h-3.5 w-3.5" />{t('复制为我的 Loop')}</Button>
        ) : (
          <>
            <Button size="sm" onClick={save} loading={saving} disabled={!dirty}><Save className="h-3.5 w-3.5" />{t('保存')}</Button>
            <Tip content={t('删除')}><Button size="icon" variant="ghost" className="text-danger" onClick={async () => {
              if (!(await confirmAction({ title: t('删除这个 Loop？'), danger: true }))) return
              await del(`/loops/${graph.id}`)
              await qc.invalidateQueries({ queryKey: ['loops'] })
              navigate('loops')
            }}><Trash2 className="h-3.5 w-3.5" /></Button></Tip>
          </>
        )}
        <Button size="sm" variant="primary" disabled={errors.length > 0} onClick={() => setDialog('run')}><Play className="h-3.5 w-3.5" />{t('运行')}</Button>
      </div>
      {graph.description && <div className="border-b border-border px-4 py-1.5 text-xs text-muted">{t(graph.description)}</div>}
      <div className="flex min-h-0 flex-1">
        {!readOnly && <Palette />}
        <div className="relative min-w-0 flex-1">
          <LoopCanvas graph={graph} editable={!readOnly} issues={issues} onChange={change} onSelect={setSelection} />
        </div>
        <div className="w-[340px] shrink-0 overflow-y-auto border-l border-border bg-surface/60">
          {selectedNode ? <NodeInspector key={selectedNode.id} node={selectedNode} graph={graph} readOnly={readOnly}
            onChange={(n) => change({ ...graph, nodes: graph.nodes.map((x) => (x.id === n.id ? n : x)) })} /> :
            selectedEdge ? <EdgeInspector key={selectedEdge.id} edge={selectedEdge} graph={graph} readOnly={readOnly}
              onChange={(e) => change({ ...graph, edges: graph.edges.map((x) => (x.id === e.id ? e : x)) })} /> :
              <GraphHelp graph={graph} issues={issues} />}
        </div>
      </div>
      {dialog === 'run' && <RunDialog graph={graph} dirty={dirty} onClose={() => setDialog(null)} />}
      {dialog === 'inputs' && <InputsDialog graph={graph} readOnly={readOnly} onClose={() => setDialog(null)} onChange={(g) => change(g)} />}
      {dialog === 'settings' && <SettingsDialog graph={graph} readOnly={readOnly} onClose={() => setDialog(null)} onChange={(g) => change(g)} />}
    </div>
  )
}

function Palette() {
  const { t } = useTranslation()
  return (
    <div className="w-[132px] shrink-0 overflow-y-auto border-r border-border bg-surface/40 p-2">
      <div className="px-1 pb-1.5 text-[11px] font-medium text-subtle">{t('拖入画布')}</div>
      <div className="flex flex-col gap-1">
        {PALETTE_ORDER.map((type) => {
          const meta = NODE_META[type]
          const Icon = meta.icon
          return (
            <Tip key={type} content={t(meta.description)} side="right">
              <div draggable onDragStart={(e) => { e.dataTransfer.setData('application/a4s-node', type); e.dataTransfer.effectAllowed = 'move' }}
                className="flex cursor-grab items-center gap-2 rounded-md border border-border bg-surface px-2 py-1.5 text-xs select-none active:cursor-grabbing hover:border-primary">
                <Icon className="h-3.5 w-3.5 shrink-0" style={{ color: meta.color }} /><span className="truncate">{t(meta.label)}</span>
              </div>
            </Tip>
          )
        })}
      </div>
    </div>
  )
}

function GraphHelp({ graph, issues }: { graph: LoopGraph; issues: Issue[] }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-4 p-4 text-xs">
      <div className="text-sm font-semibold">{t('Loop Engineering')}</div>
      <p className="leading-relaxed text-muted">{t('Loop 是把多个 Agent 和检查点连成的固定流程：谁先做、做完谁检查、不合格就重做、什么时候停。对话里的单个 Agent 由 Harness 决定（角色、工具、权限、上限），Loop 在它外面编排多个这样的 Agent，适合跑很久又不想盯着的任务。')}</p>
      <p className="leading-relaxed text-muted">{t('拖入节点、连线，点击节点在右侧配置。重复只能用“循环”容器表达（可设停止条件、最大轮数、预算、指标与保留/丢弃）；“并行展开”对列表中每一项运行子图。条件节点的出边填 true / false 或表达式。')}</p>
      <div>
        <div className="mb-1 font-medium">{t('输入')}</div>
        {Object.entries(graph.inputs).map(([k, v]) => <div key={k} className="font-mono text-muted">inputs.{k}<span className="ml-2 font-sans">{t(v.label)}</span></div>)}
        {!Object.keys(graph.inputs).length && <div className="text-subtle">{t('无')}</div>}
      </div>
      {issues.length > 0 && (
        <div>
          <div className="mb-1 font-medium">{t('校验结果')}</div>
          {issues.map((i, k) => <div key={k} className={cn('mb-1', i.level === 'error' ? 'text-danger' : 'text-warning')}>• {t(i.message)}{i.node ? ` (${i.node})` : ''}</div>)}
        </div>
      )}
      <VariableHelp graph={graph} />
    </div>
  )
}

function VariableHelp({ graph, node }: { graph: LoopGraph; node?: LoopNode }) {
  const { t } = useTranslation()
  const inLoop = node?.parent ? graph.nodes.find((n) => n.id === node.parent) : undefined
  return (
    <details className="text-xs">
      <summary className="cursor-pointer font-medium">{t('可用变量')}</summary>
      <div className="mt-1 flex flex-col gap-0.5 font-mono text-[11px] text-muted">
        {Object.keys(graph.inputs).map((k) => <span key={k}>{`{{inputs.${k}}}`}</span>)}
        {graph.nodes.filter((n) => !['start', 'end', 'note'].includes(n.type) && n.id !== node?.id).slice(0, 12).map((n) => (
          <span key={n.id}>{`{{nodes.${n.id}.output.summary}}`}</span>
        ))}
        <span>{'{{nodes.<id>.output.result.<field>}}'}</span>
        {inLoop?.type === 'loop' && <><span>{'{{loop.iteration}}'}</span><span>{'{{loop.best}}'}</span><span>{'{{loop.history}}'}</span></>}
        {inLoop?.type === 'parallel' && <><span>{`{{${inLoop.config.item_var || 'item'}}}`}</span><span>{'{{index}}'}</span></>}
        <span>{'{{metrics.<name>}}'}</span>
      </div>
    </details>
  )
}

function useOptions() {
  const { data: harnesses } = useQuery({ queryKey: ['harnesses'], queryFn: () => get<HarnessProfile[]>('/harnesses') })
  const { data: models } = useModels()
  const { data: loops } = useQuery({ queryKey: ['loops'], queryFn: () => get<LoopSummary[]>('/loops') })
  const { data: groups } = useQuery({ queryKey: ['harness-groups'], queryFn: () => get<ToolGroup[]>('/harness-groups') })
  return { harnesses, models: models?.models, loops, tools: groups?.flatMap((g) => g.tools) }
}

function NodeInspector({ node, graph, readOnly, onChange }: { node: LoopNode; graph: LoopGraph; readOnly: boolean; onChange: (n: LoopNode) => void }) {
  const { t } = useTranslation()
  const meta = NODE_META[node.type]
  const Icon = meta?.icon ?? Workflow
  const fields = FIELDS[node.type] ?? []
  const opts = useOptions()
  const optionList = (spec: FieldSpec): { value: string; label: string }[] => {
    if (Array.isArray(spec.options)) return spec.options
    switch (spec.options) {
      case 'harnesses': return (opts.harnesses ?? []).map((h) => ({ value: h.id, label: h.name }))
      case 'roles': return ROLES
      case 'models': return [{ value: '', label: '（运行设置 / 默认模型）' }, ...(opts.models ?? []).map((m) => ({ value: m.id, label: m.name }))]
      case 'loops': return [{ value: '', label: '—' }, ...(opts.loops ?? []).filter((l) => l.id !== graph.id).map((l) => ({ value: l.id, label: l.name }))]
      case 'tools': return [{ value: '', label: '—' }, ...(opts.tools ?? []).map((tl) => ({ value: tl.name, label: tl.name }))]
      default: return []
    }
  }
  const set = (key: string, value: any) => onChange({ ...node, config: setPath(node.config, key, value) })
  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4" style={{ color: meta?.color }} />
        <span className="text-sm font-semibold">{t(meta?.label ?? node.type)}</span>
        <span className="ml-auto font-mono text-[11px] text-subtle">{node.id}</span>
      </div>
      <Field label={t('名称')}><Input value={t(node.label)} disabled={readOnly} onChange={(e) => onChange({ ...node, label: e.target.value })} /></Field>
      {fields.map((spec) => {
        const value = getPath(node.config, spec.key)
        const label = t(spec.label)
        const hint = spec.hint ? t(spec.hint) : undefined
        switch (spec.kind) {
          case 'bool':
            return <Switch key={spec.key} checked={!!value} disabled={readOnly} onCheckedChange={(v) => set(spec.key, v)} label={<span className="text-sm">{label}</span>} />
          case 'number':
            return <Field key={spec.key} label={label} hint={hint}><Input type="number" disabled={readOnly} value={value ?? ''} onChange={(e) => set(spec.key, e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
          case 'select':
            return <Field key={spec.key} label={label} hint={hint}><Select disabled={readOnly} value={value ?? ''} onChange={(e) => set(spec.key, e.target.value || undefined)}>
              {optionList(spec).map((o) => <option key={o.value} value={o.value}>{t(o.label)}</option>)}</Select></Field>
          case 'textarea':
            return <Field key={spec.key} label={label} hint={hint}><Textarea rows={4} disabled={readOnly} value={value ?? ''} onChange={(e) => set(spec.key, e.target.value)} className="font-mono text-xs" /></Field>
          case 'prompt':
            return <Field key={spec.key} label={label} hint={hint}><CodeEditor value={value ?? ''} onChange={(v) => set(spec.key, v)} readOnly={readOnly} height="180px" /></Field>
          case 'expr':
            return <Field key={spec.key} label={label} hint={hint}><Input disabled={readOnly} value={value ?? ''} onChange={(e) => set(spec.key, e.target.value)} className="font-mono text-xs" /></Field>
          case 'list':
            return <Field key={spec.key} label={label} hint={hint}><Input disabled={readOnly} defaultValue={(value ?? []).join(', ')}
              onBlur={(e) => set(spec.key, e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} className="font-mono text-xs" placeholder="a, b, c" /></Field>
          case 'json':
            return <JsonField key={spec.key} label={label} hint={hint} value={value} readOnly={readOnly} onChange={(v) => set(spec.key, v)} />
          default:
            return <Field key={spec.key} label={label} hint={hint}><Input disabled={readOnly} value={value ?? ''} onChange={(e) => set(spec.key, e.target.value)} /></Field>
        }
      })}
      <VariableHelp graph={graph} node={node} />
    </div>
  )
}

function JsonField({ label, hint, value, readOnly, onChange }: { label: string; hint?: string; value: any; readOnly: boolean; onChange: (v: any) => void }) {
  const [text, setText] = useState(() => (value === undefined ? '' : JSON.stringify(value, null, 2)))
  const [error, setError] = useState<string | null>(null)
  return (
    <Field label={label} hint={error ? <span className="text-danger">{error}</span> : hint}>
      <CodeEditor language="json" value={text} readOnly={readOnly} height="140px" onChange={(v) => {
        setText(v)
        if (!v.trim()) {
          setError(null)
          onChange(undefined)
          return
        }
        try {
          onChange(JSON.parse(v))
          setError(null)
        } catch (err) {
          setError(String(err))
        }
      }} />
    </Field>
  )
}

function EdgeInspector({ edge, graph, readOnly, onChange }: { edge: LoopEdge; graph: LoopGraph; readOnly: boolean; onChange: (e: LoopEdge) => void }) {
  const { t } = useTranslation()
  const source = graph.nodes.find((n) => n.id === edge.source)
  const preset = ['', 'true', 'false', 'else'].includes(edge.condition ?? '') ? (edge.condition ?? '') : '__expr'
  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="text-sm font-semibold">{t('连线')}</div>
      <div className="font-mono text-xs text-muted">{edge.source} → {edge.target}</div>
      <Field label={t('条件')} hint={source?.type === 'condition' ? t('条件节点的出边：true / false / else，或任意表达式') : t('普通节点的出边也可加条件（守卫）')}>
        <Select disabled={readOnly} value={preset} onChange={(e) => onChange({ ...edge, condition: e.target.value === '__expr' ? 'nodes.' : e.target.value || null })}>
          <option value="">{t('（无条件）')}</option><option value="true">true</option><option value="false">false</option>
          <option value="else">else</option><option value="__expr">{t('表达式…')}</option>
        </Select>
      </Field>
      {preset === '__expr' && <Field label={t('表达式')}><Input disabled={readOnly} value={edge.condition ?? ''} onChange={(e) => onChange({ ...edge, condition: e.target.value })} className="font-mono text-xs" /></Field>}
      <Field label={t('显示标签（可选）')}><Input disabled={readOnly} value={edge.label} onChange={(e) => onChange({ ...edge, label: e.target.value })} /></Field>
    </div>
  )
}

function InputsDialog({ graph, readOnly, onClose, onChange }: { graph: LoopGraph; readOnly: boolean; onClose: () => void; onChange: (g: LoopGraph) => void }) {
  const { t } = useTranslation()
  const [rows, setRows] = useState(() => Object.entries(graph.inputs).map(([name, spec]) => ({ name, ...spec })))
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={t('Loop 输入')} width="max-w-2xl"
      description={t('运行时需要填写的参数；在提示中用 {{inputs.名称}} 引用。')}
      footer={!readOnly && <><Button variant="outline" onClick={onClose}>{t('取消')}</Button><Button variant="primary" onClick={() => {
        onChange({ ...graph, inputs: Object.fromEntries(rows.filter((r) => r.name).map(({ name, ...spec }) => [name, spec])) })
        onClose()
      }}>{t('确定')}</Button></>}>
      <div className="flex flex-col gap-2">
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-[1fr_1.5fr_110px_auto_auto] items-center gap-2">
            <Input value={r.name} disabled={readOnly} placeholder="name" className="font-mono text-xs" onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
            <Input value={r.label} disabled={readOnly} placeholder={t('显示名')} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
            <Select value={r.type} disabled={readOnly} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, type: e.target.value } : x)))}>
              {['text', 'string', 'number', 'boolean'].map((v) => <option key={v}>{v}</option>)}
            </Select>
            <Switch checked={r.required} disabled={readOnly} onCheckedChange={(v) => setRows(rows.map((x, j) => (j === i ? { ...x, required: v } : x)))} label={<span className="text-xs">{t('必填')}</span>} />
            {!readOnly && <Button size="icon" variant="ghost" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 className="h-3.5 w-3.5" /></Button>}
          </div>
        ))}
        {!readOnly && <Button size="sm" variant="outline" className="self-start" onClick={() => setRows([...rows, { name: '', label: '', type: 'text', required: false, default: null }])}><Plus className="h-3.5 w-3.5" />{t('添加输入')}</Button>}
      </div>
    </Dialog>
  )
}

function SettingsDialog({ graph, readOnly, onClose, onChange }: { graph: LoopGraph; readOnly: boolean; onClose: () => void; onChange: (g: LoopGraph) => void }) {
  const { t } = useTranslation()
  const { data: models } = useModels()
  const [s, setS] = useState(graph.settings)
  const [description, setDescription] = useState(graph.description)
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={t('Loop 设置')}
      footer={!readOnly && <><Button variant="outline" onClick={onClose}>{t('取消')}</Button><Button variant="primary" onClick={() => {
        onChange({ ...graph, settings: s, description })
        onClose()
      }}>{t('确定')}</Button></>}>
      <div className="flex flex-col gap-3">
        <Field label={t('描述')}><Textarea rows={2} disabled={readOnly} value={t(description)} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label={t('预算上限（美元）')}><Input type="number" disabled={readOnly} value={s.max_cost_usd ?? ''} onChange={(e) => setS({ ...s, max_cost_usd: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
        <Field label={t('记忆模式')}><Select disabled={readOnly} value={s.memory_mode ?? ''} onChange={(e) => setS({ ...s, memory_mode: (e.target.value || null) as any })}>
          <option value="">{t('（跟随项目设置）')}</option>{MEMORY_MODES.map((m) => <option key={m.value} value={m.value}>{t(m.label)}</option>)}</Select></Field>
        <Field label={t('默认模型')}><Select disabled={readOnly} value={s.model_id ?? ''} onChange={(e) => setS({ ...s, model_id: e.target.value || null })}>
          <option value="">{t('（全局默认 / 角色映射）')}</option>{models?.models.map((m) => <option key={m.id} value={m.id}>{t(m.name)}</option>)}</Select></Field>
        <Switch checked={s.auto_commit} disabled={readOnly} onCheckedChange={(v) => setS({ ...s, auto_commit: v })} label={<span className="text-sm">{t('每个节点完成后 git 提交')}</span>} />
      </div>
    </Dialog>
  )
}

function RunDialog({ graph, dirty, onClose }: { graph: LoopGraph; dirty: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const current = useCurrentProject()
  const { data: projects } = useProjects()
  const { data: models } = useModels()
  const [projectId, setProjectId] = useState(current?.id ?? projects?.[0]?.id ?? '')
  const [inputs, setInputs] = useState<Record<string, any>>(() => Object.fromEntries(Object.entries(graph.inputs).map(([k, v]) => [k, v.default ?? ''])))
  const [auto, setAuto] = useState(false)
  const [maxCost, setMaxCost] = useState<string>(graph.settings.max_cost_usd ? String(graph.settings.max_cost_usd) : '')
  const [modelId, setModelId] = useState('')
  const [busy, setBusy] = useState(false)
  const start = async () => {
    setBusy(true)
    try {
      const body: Record<string, any> = { project_id: projectId, inputs, auto_mode: auto, max_cost_usd: maxCost ? Number(maxCost) : null, model_id: modelId || null }
      if (dirty) body.graph = graph
      else body.loop_id = graph.id
      const run = await post<{ id: string }>('/runs', body)
      navigate('runs', run.id)
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={t('运行「{{name}}」', { name: t(graph.name) })} width="max-w-xl"
      footer={<><Button variant="outline" onClick={onClose}>{t('取消')}</Button>
        <Button variant="primary" loading={busy} disabled={!projectId} onClick={start}><Play className="h-4 w-4" />{t('开始运行')}</Button></>}>
      <div className="flex flex-col gap-3">
        {!projects?.length ? (
          <div className="rounded-md border border-warning/40 bg-warning/8 p-3 text-sm">{t('运行 Loop 需要一个项目工作区。')}
            <button className="ml-1 text-primary underline" onClick={() => navigate('projects')}>{t('去创建项目')}</button></div>
        ) : (
          <Field label={t('项目')}><Select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></Field>
        )}
        {Object.entries(graph.inputs).map(([k, spec]) => (
          <Field key={k} label={`${t(spec.label || k)}${spec.required ? ' *' : ''}`}>
            {spec.type === 'text' ? <Textarea rows={3} value={inputs[k] ?? ''} onChange={(e) => setInputs({ ...inputs, [k]: e.target.value })} />
              : <Input value={inputs[k] ?? ''} onChange={(e) => setInputs({ ...inputs, [k]: e.target.value })} />}
          </Field>
        ))}
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('预算上限（美元）')}><Input type="number" value={maxCost} onChange={(e) => setMaxCost(e.target.value)} placeholder={t('不限')} /></Field>
          <Field label={t('模型')}><Select value={modelId} onChange={(e) => setModelId(e.target.value)}>
            <option value="">{t('（Loop 设置 / 默认）')}</option>{models?.models.map((m) => <option key={m.id} value={m.id}>{t(m.name)}</option>)}</Select></Field>
        </div>
        <Switch checked={auto} onCheckedChange={setAuto} label={<span className="text-sm">{t('全自动（跳过所有人工检查点，自动批准）')}</span>} />
        {dirty && <div className="text-xs text-warning">{t('有未保存的修改：本次运行使用当前画布上的版本。')}</div>}
        <FileInputHint />
      </div>
    </Dialog>
  )
}

function FileInputHint() {
  const { t } = useTranslation()
  return <div className="flex items-center gap-1.5 text-[11px] text-subtle"><FileInput className="h-3 w-3" />{t('需要的数据文件请先放进项目的 data/ 目录。')}</div>
}
