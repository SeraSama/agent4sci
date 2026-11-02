import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BookMarked, Check, ChevronRight, Cpu, File, FileText, Folder, FolderOpen, FolderPlus, GitCommitHorizontal,
  Lightbulb, MessageSquare, Pencil, Plus, RotateCcw, ShieldCheck, Sparkles, Trash2, TriangleAlert, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { navigate, useRoute } from '@/app/router'
import { askForText, confirmAction } from '@/components/confirm'
import { useContextMenu } from '@/components/ContextMenu'
import { Markdown } from '@/components/Markdown'
import { Badge, Button, Card, Dialog, Empty, Field, Input, PageHeader, Select, SectionTitle, Spinner, Switch,
  TabPanel, Tabs, Textarea } from '@/components/ui'
import { del, errorMessage, get, post, put } from '@/lib/api'
import { useEvents } from '@/lib/events'
import { useApp, useProjects } from '@/lib/store'
import type { MemoryMode, Project } from '@/lib/types'
import { cn, formatBytes, formatTime, toast } from '@/lib/utils'
import { MEMORY_MODES } from './ChatPage'

interface Domain { id: string; label: string; description: string }

export default function ProjectsPage() {
  const { t } = useTranslation()
  const { data: projects } = useProjects()
  const route = useRoute()
  const { projectId, setProject } = useApp()
  const [creating, setCreating] = useState(false)
  const selected = route.param ?? projectId ?? projects?.[0]?.id
  const qc = useQueryClient()

  const openExisting = async () => {
    let path: string | null = null
    if (window.a4s) path = await window.a4s.pickFolder()
    else path = await askForText({ title: t('打开已有项目'), input: { label: t('输入已有文件夹的完整路径'), required: true } })
    if (!path) return
    try {
      const p = await post<Project>('/projects/open', { path })
      await qc.invalidateQueries({ queryKey: ['projects'] })
      setProject(p.id)
      navigate('projects', p.id)
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex w-[248px] shrink-0 flex-col border-r border-border bg-surface/60">
        <div className="flex gap-2 px-3 pt-4 pb-2">
          <Button variant="primary" size="sm" className="flex-1" onClick={() => setCreating(true)}><Plus className="h-3.5 w-3.5" />{t('新建项目')}</Button>
          <Button size="sm" variant="outline" onClick={openExisting} title={t('打开已有文件夹')}><FolderOpen className="h-3.5 w-3.5" /></Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {projects?.map((p) => <ProjectRow key={p.id} project={p} active={p.id === selected} />)}
          {!projects?.length && <div className="px-3 py-8 text-center text-xs text-subtle">{t('还没有项目')}</div>}
        </div>
      </div>
      {selected ? <ProjectDetail key={selected} id={selected} /> : (
        <div className="flex-1"><Empty icon={<FolderPlus className="h-10 w-10" />} title={t('创建第一个研究项目')}
          hint={t('项目 = 一个工作区文件夹：文献、数据、代码、结果、图表、论文与项目记忆都在里面，并用 git 自动记录每一步。')}
          action={<Button variant="primary" onClick={() => setCreating(true)}><Plus className="h-4 w-4" />{t('新建项目')}</Button>} /></div>
      )}
      {creating && <NewProjectDialog onClose={() => setCreating(false)} />}
    </div>
  )
}

/** A project in the list. Right-click to rename, reveal, or remove it from the app. */
function ProjectRow({ project, active }: { project: Project; active: boolean }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { projectId, setProject } = useApp()

  const rename = async () => {
    const name = await askForText({ title: t('重命名项目'), input: { label: t('名称'), initial: project.name, required: true } })
    if (!name) return
    await put(`/projects/${project.id}`, { name })
    await qc.invalidateQueries({ queryKey: ['projects'] })
  }

  const remove = async () => {
    const ok = await confirmAction({
      title: t('从 Agent4Sci 里移除「{{n}}」？', { n: project.name }),
      description: t('只是从列表里移除，磁盘上的文件夹和它的 git 历史都留着。随时可以用「打开已有文件夹」加回来。'),
      danger: true,
      confirmLabel: t('移除'),
    })
    if (!ok) return
    await del(`/projects/${project.id}`)
    await qc.invalidateQueries({ queryKey: ['projects'] })
    if (projectId === project.id) setProject(null)
    if (active) navigate('projects')
  }

  const menu = useContextMenu([
    { label: t('新对话'), icon: <MessageSquare className="h-3.5 w-3.5" />, onSelect: async () => {
      const conv = await post<{ id: string }>('/conversations', { project_id: project.id })
      navigate('chat', conv.id)
    } },
    ...(window.a4s ? [{ label: t('在文件夹中显示'), icon: <FolderOpen className="h-3.5 w-3.5" />,
      onSelect: () => void window.a4s?.openPath(project.path) }] : []),
    { label: t('重命名'), icon: <Pencil className="h-3.5 w-3.5" />, onSelect: () => void rename() },
    { label: t('从列表移除'), icon: <Trash2 className="h-3.5 w-3.5" />, danger: true, separatorBefore: true,
      onSelect: () => void remove() },
  ])

  return (
    <>
      <button {...menu.trigger} onClick={() => { setProject(project.id); navigate('projects', project.id) }}
        className={cn('flex w-full flex-col items-start rounded-md px-2.5 py-2 text-left transition',
          active ? 'bg-primary/12' : 'hover:bg-surface-2')}>
        <span className="flex w-full items-center gap-2 text-sm font-medium"><Folder className="h-3.5 w-3.5 shrink-0 text-primary" /><span className="truncate">{project.name}</span></span>
        <span className="mt-0.5 flex flex-wrap gap-1 pl-5.5">{project.domains.map((d) => <Badge key={d}>{d}</Badge>)}</span>
      </button>
      {menu.menu}
    </>
  )
}

function NewProjectDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { setProject } = useApp()
  const { data: domains } = useQuery({ queryKey: ['domains'], queryFn: () => get<Domain[]>('/domains') })
  const { data: info } = useQuery({ queryKey: ['info'], queryFn: () => get<{ projects_dir: string }>('/info') })
  const { data: layout } = useQuery({
    queryKey: ['workspace-dirs'],
    queryFn: () => get<{ dirs: string[]; required: string[]; recommended: string[] }>('/workspace-dirs'),
  })
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [description, setDescription] = useState('')
  const [chosen, setChosen] = useState<string[]>(['general'])
  const [venv, setVenv] = useState(true)
  const [packs, setPacks] = useState(false)
  const [busy, setBusy] = useState(false)
  const [dirs, setDirs] = useState<string[] | null>(null)
  // Until the user touches anything, follow the backend's recommendation.
  const optional = (layout?.dirs ?? []).filter((d) => !layout?.required.includes(d))
  const picked = dirs ?? layout?.recommended ?? []

  const create = async () => {
    setBusy(true)
    try {
      const p = await post<Project>('/projects', {
        name, path: path || undefined, description, domains: chosen, create_venv: venv,
        ...(dirs ? { dirs } : {}),
      })
      await qc.invalidateQueries({ queryKey: ['projects'] })
      setProject(p.id)
      navigate('projects', p.id)
      toast.success(t('项目已创建'))
      if (packs) {
        toast.info(t('正在后台安装领域技能包…'))
        for (const d of chosen) {
          post<any[]>(`/skills/packs/${d}/install`, { project_id: p.id, scope: 'project' })
            .then((r) => toast.success(t('{{d}} 技能包：安装 {{n}} 个', { d, n: r.filter((x) => x.status === 'installed').length })))
            .catch((err) => toast.error(errorMessage(err)))
        }
      }
      onClose()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={t('新建研究项目')} width="max-w-2xl"
      footer={<><Button variant="outline" onClick={onClose}>{t('取消')}</Button>
        <Button variant="primary" disabled={!name.trim()} loading={busy} onClick={create}><Check className="h-4 w-4" />{t('创建')}</Button></>}>
      <div className="flex flex-col gap-4">
        <Field label={t('项目名称')}><Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder={t('例如：海马 theta 与工作记忆')} /></Field>
        <Field label={t('位置')} hint={t('留空则放在 {{dir}} 下', { dir: info?.projects_dir ?? '' })}>
          <div className="flex gap-2">
            <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder={t('默认位置')} className="font-mono text-xs" />
            {window.a4s && <Button variant="outline" onClick={async () => {
              const dir = await window.a4s!.pickFolder()
              if (dir) setPath(`${dir}/${name || 'project'}`)
            }}><FolderOpen className="h-4 w-4" /></Button>}
          </div>
        </Field>
        <Field label={t('研究领域（决定默认工具、技能与提示）')}>
          <div className="grid grid-cols-2 gap-2">
            {domains?.map((d) => (
              <label key={d.id} className={cn('flex cursor-pointer gap-2 rounded-md border p-2.5 text-sm transition',
                chosen.includes(d.id) ? 'border-primary bg-primary/6' : 'border-border')}>
                <input type="checkbox" checked={chosen.includes(d.id)} onChange={(e) =>
                  setChosen(e.target.checked ? [...chosen, d.id] : chosen.filter((x) => x !== d.id))} />
                <div><div className="font-medium">{t(d.label)}</div><div className="text-xs text-muted">{t(d.description)}</div></div>
              </label>
            ))}
          </div>
        </Field>
        <Field label={t('描述（可选）')}><Textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label={t('先建哪些文件夹')}
          hint={t('只是预先建好；工具用到别的目录时会自己创建，所以少勾不影响功能。{{r}} 由应用自己管理，始终创建。',
            { r: (layout?.required ?? []).join('、') })}>
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap gap-1.5">
              {optional.map((d) => (
                <button key={d} type="button" onClick={() => setDirs(picked.includes(d) ? picked.filter((x) => x !== d) : [...picked, d])}
                  className={cn('inline-flex items-center gap-1 rounded-md border px-2 py-1 font-mono text-[11px] transition',
                    picked.includes(d) ? 'border-primary bg-primary/10 text-fg' : 'border-border text-muted hover:border-primary/50')}>
                  {picked.includes(d) && <Check className="h-3 w-3 shrink-0 text-primary" />}{d}/
                </button>
              ))}
            </div>
            <div className="flex gap-3 text-[11px]">
              <button type="button" className="text-primary underline" onClick={() => setDirs(layout?.recommended ?? [])}>{t('推荐')}</button>
              <button type="button" className="text-primary underline" onClick={() => setDirs(optional)}>{t('全选')}</button>
              <button type="button" className="text-primary underline" onClick={() => setDirs([])}>{t('全不选')}</button>
            </div>
          </div>
        </Field>
        <Switch checked={venv} onCheckedChange={setVenv} label={<span className="text-sm">{t('创建 Python 环境（uv：numpy、pandas、scipy、matplotlib、ipykernel）')}</span>} />
        <Switch checked={packs} onCheckedChange={setPacks} label={<span className="text-sm">{t('同时安装所选领域的精选技能包（需联网，来自 GitHub）')}</span>} />
      </div>
    </Dialog>
  )
}

function ProjectDetail({ id }: { id: string }) {
  const { t } = useTranslation()
  const route = useRoute()
  const [tab, setTab] = useState(route.query.get('tab') ?? 'overview')
  const { data: project, refetch } = useQuery({ queryKey: ['project', id], queryFn: () => get<Project>(`/projects/${id}`) })
  if (!project) return <div className="flex flex-1 items-center justify-center"><Spinner /></div>
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <PageHeader title={project.name} description={<span className="font-mono">{project.path}</span>} actions={<>
        {window.a4s && <Button size="sm" variant="outline" onClick={() => window.a4s!.openPath(project.path)}><FolderOpen className="h-3.5 w-3.5" />{t('在文件夹中显示')}</Button>}
        <Button size="sm" onClick={async () => {
          const conv = await post<{ id: string }>('/conversations', { project_id: project.id })
          navigate('chat', conv.id)
        }}><MessageSquare className="h-3.5 w-3.5" />{t('新对话')}</Button>
        <Button size="sm" variant="primary" onClick={() => navigate('loops')}><Workflow className="h-3.5 w-3.5" />{t('运行 Loop')}</Button>
      </>} />
      <Tabs value={tab} onValueChange={setTab} className="flex-1" tabs={[
        { value: 'overview', label: t('概览') }, { value: 'files', label: t('文件') }, { value: 'audit', label: t('自检') },
        { value: 'findings', label: t('发现账本') }, { value: 'refs', label: t('参考文献') }, { value: 'history', label: t('版本历史') },
      ]}>
        <TabPanel value="overview" className="px-6 py-5"><Overview project={project} onChange={() => void refetch()} /></TabPanel>
        <TabPanel value="files" className="flex min-h-0"><FileBrowser projectId={project.id} /></TabPanel>
        <TabPanel value="audit" className="px-6 py-5"><Audit projectId={project.id} /></TabPanel>
        <TabPanel value="findings" className="px-6 py-5"><Findings projectId={project.id} /></TabPanel>
        <TabPanel value="refs" className="px-6 py-5"><References projectId={project.id} /></TabPanel>
        <TabPanel value="history" className="px-6 py-5"><History projectId={project.id} /></TabPanel>
      </Tabs>
    </div>
  )
}

function Overview({ project, onChange }: { project: Project; onChange: () => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [venvLog, setVenvLog] = useState<string[]>([])
  const [venvBusy, setVenvBusy] = useState(false)
  useEvents(`project:${project.id}`, (e) => {
    if (e.type === 'venv.progress') setVenvLog((l) => [...l, e.data.step])
    if (e.type === 'venv.done') {
      setVenvBusy(false)
      if (e.data.ok) toast.success(t('Python 环境已就绪'))
      else toast.error(t('创建环境失败：{{e}}', { e: String(e.data.error).slice(0, 200) }))
      onChange()
    }
  })
  const save = async (patch: Record<string, any>) => {
    try {
      await put(`/projects/${project.id}`, patch)
      onChange()
      await qc.invalidateQueries({ queryKey: ['projects'] })
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }
  const env = project.environment ?? {}
  return (
    <div className="grid max-w-5xl gap-5 lg:grid-cols-2">
      <Card className="p-4">
        <SectionTitle>{t('计算环境')}</SectionTitle>
        <div className="flex flex-col gap-2 text-sm">
          <div className="flex items-center gap-2"><Cpu className="h-4 w-4 text-muted" />{env.chip ?? env.machine} · {env.cpus} {t('核')}{env.memory_gb ? ` · ${env.memory_gb} GB` : ''}</div>
          <div className="text-xs text-muted">{t('加速器')}：{env.accelerator ?? '—'}</div>
          <div className="flex items-center gap-2">
            {project.venv ? <Badge tone="success"><Check className="h-3 w-3" />{t('Python 环境已创建')}</Badge> : <Badge tone="warning">{t('尚无 Python 环境')}</Badge>}
            {project.git ? <Badge tone="info"><GitCommitHorizontal className="h-3 w-3" />git</Badge> : <Badge>{t('无 git')}</Badge>}
          </div>
          {!project.venv && (
            <Button size="sm" className="self-start" loading={venvBusy} onClick={async () => {
              setVenvBusy(true)
              setVenvLog([])
              await post(`/projects/${project.id}/venv`)
            }}>{t('创建 Python 环境')}</Button>
          )}
          {venvLog.length > 0 && <div className="font-mono text-[11px] text-muted">{venvLog.map((l, i) => <div key={i}>› {l}</div>)}</div>}
        </div>
      </Card>
      <Card className="p-4">
        <SectionTitle>{t('项目设置')}</SectionTitle>
        <div className="flex flex-col gap-3">
          <Field label={t('新对话的默认记忆模式')}>
            <Select value={project.settings.default_memory_mode}
              onChange={(e) => save({ settings: { default_memory_mode: e.target.value as MemoryMode } })}>
              {MEMORY_MODES.map((m) => <option key={m.value} value={m.value}>{t(m.label)}</option>)}
            </Select>
          </Field>
          <Switch checked={project.settings.auto_commit} onCheckedChange={(v) => save({ settings: { auto_commit: v } })}
            label={<span className="text-sm">{t('每个 Loop 节点完成后自动 git 提交')}</span>} />
          <Switch checked={project.settings.trusted} onCheckedChange={(v) => save({ settings: { trusted: v } })}
            label={<span className="text-sm">{t('信任此文件夹（加载其中 .agents/skills 与 .claude/skills）')}</span>} />
          <Field label={t('透传给命令行的环境变量（逗号分隔）')} hint={t('其余包含 KEY/TOKEN/SECRET 的变量都会被屏蔽')}>
            <Input defaultValue={project.settings.env_passthrough.join(', ')} onBlur={(e) => save({ settings: {
              env_passthrough: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) } })} />
          </Field>
        </div>
      </Card>
      <Card className="p-4 lg:col-span-2">
        <SectionTitle>{t('研究领域')}</SectionTitle>
        <div className="flex flex-wrap items-center gap-2">
          {project.domains.map((d) => <Badge key={d} tone="primary">{d}</Badge>)}
          <Button size="sm" variant="ghost" onClick={() => navigate('skills', undefined, { tab: 'discover' })}><Sparkles className="h-3.5 w-3.5" />{t('安装领域技能包')}</Button>
        </div>
        {project.description && <p className="mt-3 text-sm text-muted">{project.description}</p>}
      </Card>
    </div>
  )
}

function FileBrowser({ projectId }: { projectId: string }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState<Set<string>>(new Set(['.']))
  const [selected, setSelected] = useState<string | null>(null)
  return (
    <div className="flex min-h-0 flex-1">
      <div className="w-72 shrink-0 overflow-y-auto border-r border-border py-2 text-sm">
        <Tree projectId={projectId} path="." depth={0} open={open} setOpen={setOpen} selected={selected} onSelect={setSelected} />
      </div>
      <div className="min-w-0 flex-1 overflow-y-auto">
        {selected ? <FilePreview projectId={projectId} path={selected} /> :
          <Empty icon={<FileText className="h-8 w-8" />} title={t('选择一个文件预览')} />}
      </div>
    </div>
  )
}

function Tree({ projectId, path, depth, open, setOpen, selected, onSelect }: {
  projectId: string; path: string; depth: number; open: Set<string>; setOpen: (s: Set<string>) => void
  selected: string | null; onSelect: (p: string) => void
}) {
  const { data } = useQuery({
    queryKey: ['files', projectId, path],
    queryFn: () => get<{ entries: { name: string; path: string; dir: boolean; size: number }[] }>('/files', { project_id: projectId, path }),
  })
  return (
    <>
      {data?.entries.map((e) => (
        <div key={e.path}>
          <button onClick={() => {
            if (e.dir) {
              const next = new Set(open)
              if (next.has(e.path)) next.delete(e.path); else next.add(e.path)
              setOpen(next)
            } else onSelect(e.path)
          }}
            className={cn('flex w-full items-center gap-1.5 py-0.5 pr-2 text-left hover:bg-surface-2', selected === e.path && 'bg-primary/12')}
            style={{ paddingLeft: 8 + depth * 14 }}>
            {e.dir ? <ChevronRight className={cn('h-3 w-3 shrink-0 text-subtle transition', open.has(e.path) && 'rotate-90')} /> : <span className="w-3" />}
            {e.dir ? <Folder className="h-3.5 w-3.5 shrink-0 text-primary/80" /> : <File className="h-3.5 w-3.5 shrink-0 text-subtle" />}
            <span className="min-w-0 flex-1 truncate">{e.name}</span>
            {!e.dir && <span className="shrink-0 text-[10px] text-subtle">{formatBytes(e.size)}</span>}
          </button>
          {e.dir && open.has(e.path) && <Tree projectId={projectId} path={e.path} depth={depth + 1} open={open} setOpen={setOpen} selected={selected} onSelect={onSelect} />}
        </div>
      ))}
    </>
  )
}

export function FilePreview({ projectId, conversationId, path }: { projectId?: string; conversationId?: string; path: string }) {
  const { t } = useTranslation()
  const { data, error } = useQuery({
    queryKey: ['file', projectId, conversationId, path],
    queryFn: () => get<{ kind: string; content?: string; mime?: string; size: number; truncated?: boolean }>('/file', {
      project_id: projectId, conversation_id: conversationId, path }),
  })
  if (error) return <div className="p-6 text-sm text-danger">{errorMessage(error)}</div>
  if (!data) return <div className="p-6"><Spinner /></div>
  return (
    <div className="flex flex-col">
      <div className="sticky top-0 flex items-center gap-2 border-b border-border bg-bg/95 px-5 py-2 font-mono text-xs text-muted backdrop-blur">
        {path}<span className="ml-auto">{formatBytes(data.size)}</span>
      </div>
      <div className="px-6 py-4">
        {data.kind === 'image' ? <img src={`data:${data.mime};base64,${data.content}`} className="max-w-full rounded border border-border bg-white" /> :
          data.kind === 'binary' ? <div className="text-sm text-muted">{t('二进制文件，无法预览')}</div> :
            /\.(md|markdown)$/i.test(path) ? <Markdown className="max-w-3xl text-sm" text={data.content ?? ''} /> :
              <pre className="overflow-x-auto rounded-md bg-code p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">{data.content}</pre>}
        {data.truncated && <div className="mt-2 text-xs text-warning">{t('文件较大，只显示前 2MB')}</div>}
      </div>
    </div>
  )
}

function Findings({ projectId }: { projectId: string }) {
  const { t } = useTranslation()
  const { data } = useQuery({ queryKey: ['findings', projectId], queryFn: () => get<any[]>(`/projects/${projectId}/findings`) })
  if (!data?.length) return <Empty icon={<Lightbulb className="h-8 w-8" />} title={t('发现账本为空')}
    hint={t('Agent 用 record_finding 记录的每条结论与证据会出现在这里；证据会自动核验（文献键、结果文件的 JSON 指针、文件路径）。')} />
  return (
    <div className="flex max-w-4xl flex-col gap-2">
      {data.map((f) => (
        <Card key={f.id} className="p-3">
          <div className="flex items-start gap-2">
            <span className="font-mono text-xs text-subtle">{f.id}</span>
            <div className="min-w-0 flex-1 text-sm">{f.statement}</div>
            <Badge tone={f.verified ? 'success' : 'warning'}>{f.verified ? t('证据已核验') : t('证据未核验')}</Badge>
            <Badge>{f.kind}</Badge><Badge>{f.confidence}</Badge>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1 pl-9">{(f.checks ?? []).map((c: string, i: number) => <span key={i} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-muted">{c}</span>)}</div>
        </Card>
      ))}
    </div>
  )
}

function References({ projectId }: { projectId: string }) {
  const { t } = useTranslation()
  const { data } = useQuery({ queryKey: ['refs', projectId], queryFn: () => get<{ key: string; entry: string }[]>(`/projects/${projectId}/references`) })
  if (!data?.length) return <Empty icon={<BookMarked className="h-8 w-8" />} title={t('还没有参考文献')}
    hint={t('只有通过 add_reference 从 DOI / arXiv / PubMed 取回的真实记录才会写入 references.bib。')} />
  return (
    <div className="flex max-w-4xl flex-col gap-2">
      {data.map((r) => (
        <details key={r.key} className="rounded-md border border-border bg-surface px-3 py-2">
          <summary className="cursor-pointer font-mono text-sm">[@{r.key}]</summary>
          <pre className="mt-2 overflow-x-auto font-mono text-[11px] whitespace-pre-wrap text-muted">{r.entry}</pre>
        </details>
      ))}
    </div>
  )
}

function History({ projectId }: { projectId: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['git', projectId], queryFn: () => get<{ sha: string; time: number; message: string }[]>(`/projects/${projectId}/git`) })
  if (!data?.length) return <Empty icon={<GitCommitHorizontal className="h-8 w-8" />} title={t('还没有提交记录')} />
  return (
    <div className="flex max-w-3xl flex-col">
      {data.map((c, i) => (
        <div key={c.sha} className="flex items-center gap-3 border-l-2 border-border py-2 pl-4">
          <GitCommitHorizontal className="-ml-[27px] h-4 w-4 shrink-0 rounded-full bg-bg text-primary" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm">{c.message}</div>
            <div className="font-mono text-[11px] text-subtle">{c.sha.slice(0, 8)} · {formatTime(c.time)}</div>
          </div>
          {i > 0 && <Button size="sm" variant="ghost" onClick={async () => {
            if (!(await confirmAction({ title: t('回滚到这个版本？'),
              description: t('当前改动会先自动保存为一个提交。'), danger: true, confirmLabel: t('回滚') }))) return
            await post(`/projects/${projectId}/git/rollback`, { sha: c.sha })
            await qc.invalidateQueries({ queryKey: ['git', projectId] })
            toast.success(t('已回滚'))
          }}><RotateCcw className="h-3.5 w-3.5" />{t('回滚到此')}</Button>}
        </div>
      ))}
    </div>
  )
}

export function useDeleteProject() {
  return async (id: string) => del(`/projects/${id}`)
}

export { useEffect }

// ---- 自检 -----------------------------------------------------------------------------------------

interface AuditItem { message: string; file: string | null }
interface AuditReport {
  ts: number | null
  passed?: boolean
  path?: string
  checked?: Record<string, string[]>
  errors?: AuditItem[]
  warnings?: AuditItem[]
  findings?: { total: number; drifted: any[]; unverifiable: any[]; weak: any[] }
}

/** User-triggered quality check: it reports, it never blocks a run. */
function Audit({ projectId }: { projectId: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [running, setRunning] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const { data } = useQuery({
    queryKey: ['audit', projectId],
    queryFn: () => get<AuditReport>(`/projects/${projectId}/audit`),
  })
  const run = async () => {
    setRunning(true)
    try {
      const report = await post<AuditReport>(`/projects/${projectId}/audit`, {})
      qc.setQueryData(['audit', projectId], report)
      toast[report.passed ? 'success' : 'info'](report.passed ? t('自检通过') : t('自检发现 {{n}} 个问题', {
        n: (report.errors?.length ?? 0) + (report.warnings?.length ?? 0) }))
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setRunning(false)
    }
  }

  const errors = data?.errors ?? []
  const warnings = data?.warnings ?? []
  const f = data?.findings
  const scope = Object.entries(data?.checked ?? {}).filter(([, v]) => v?.length)

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <div className="flex items-start gap-3">
        <p className="flex-1 text-xs text-muted">
          {t('检查这个项目现在是否经得起看：引用的文献键是否都在参考文献里、结果数字是否有出处、占位符是否都渲染了、必需产物是否齐全，以及发现账本里的证据按当前文件重新核验一遍（记录时通过、现在不通过的会单独列出）。只报告，不会拦住任何运行。')}
        </p>
        <Button variant="primary" loading={running} onClick={run}><ShieldCheck className="h-4 w-4" />{t('运行自检')}</Button>
      </div>

      {!data?.ts ? (
        <Empty icon={<ShieldCheck className="h-8 w-8" />} title={t('还没有自检记录')}
          hint={t('点"运行自检"开始；报告会保存在项目的 .a4s/audit/ 下，方便回看。')} />
      ) : (
        <>
          <Card className={cn('flex items-center gap-3 p-3', data.passed ? 'border-success/40 bg-success/8' : 'border-warning/40 bg-warning/8')}>
            {data.passed ? <Check className="h-5 w-5 text-success" /> : <TriangleAlert className="h-5 w-5 text-warning" />}
            <div className="flex-1 text-sm">
              {data.passed ? t('没有发现阻断性问题') : t('{{e}} 个错误 · {{w}} 个警告', { e: errors.length, w: warnings.length })}
            </div>
            <span className="text-[11px] text-subtle">{t('检查于 {{time}}', { time: formatTime(data.ts) })}</span>
          </Card>

          {errors.length > 0 && <AuditList title={t('错误（会让结论站不住）')} tone="danger" items={errors} onOpen={setOpen} />}
          {warnings.length > 0 && <AuditList title={t('警告（建议处理）')} tone="warning" items={warnings} onOpen={setOpen} />}

          {f && f.total > 0 && (
            <Card className="p-3">
              <SectionTitle>{t('发现账本重新核验')}</SectionTitle>
              <div className="mt-1 flex flex-wrap gap-2 text-xs">
                <Badge>{t('共 {{n}} 条', { n: f.total })}</Badge>
                <Badge tone={f.drifted.length ? 'danger' : 'success'}>{t('证据已失效 {{n}}', { n: f.drifted.length })}</Badge>
                <Badge tone={f.unverifiable.length ? 'warning' : undefined}>{t('无法核验 {{n}}', { n: f.unverifiable.length })}</Badge>
                <Badge tone={f.weak.length ? 'warning' : undefined}>{t('仅弱证据 {{n}}', { n: f.weak.length })}</Badge>
              </div>
              {[...f.drifted, ...f.unverifiable].map((item: any) => (
                <div key={item.id} className="mt-2 border-l-2 border-danger/50 pl-2.5 text-xs">
                  <span className="font-mono text-subtle">{item.id}</span> {item.statement}
                  {(item.notes ?? []).map((n: string, i: number) => <div key={i} className="font-mono text-[11px] text-danger">{n}</div>)}
                </div>
              ))}
            </Card>
          )}

          {scope.length > 0 && (
            <details className="text-xs text-muted">
              <summary className="cursor-pointer">{t('这次检查了哪些文件')}</summary>
              <div className="mt-1.5 flex flex-col gap-1 pl-3">
                {scope.map(([kind, files]) => (
                  <div key={kind}><span className="text-subtle">{kind}:</span> {files.join('、')}</div>
                ))}
              </div>
            </details>
          )}
        </>
      )}
      {open && (
        <Dialog open onOpenChange={(o) => !o && setOpen(null)} width="max-w-4xl" title={open}>
          <div className="max-h-[70vh] overflow-y-auto"><FilePreview projectId={projectId} path={open} /></div>
        </Dialog>
      )}
    </div>
  )
}

function AuditList({ title, tone, items, onOpen }: {
  title: string
  tone: 'danger' | 'warning'
  items: AuditItem[]
  onOpen: (path: string) => void
}) {
  const { t } = useTranslation()
  return (
    <Card className="p-3">
      <SectionTitle>{title}</SectionTitle>
      <div className="mt-1 flex flex-col gap-1.5">
        {items.map((item, i) => (
          <div key={i} className={cn('flex items-start gap-2 rounded-md px-2 py-1.5 text-xs',
            tone === 'danger' ? 'bg-danger/8 text-danger' : 'bg-warning/8 text-muted')}>
            <span className="min-w-0 flex-1 whitespace-pre-wrap">{item.message}</span>
            {item.file && (
              <button className="shrink-0 underline hover:no-underline" onClick={() => onOpen(item.file!)}>{t('打开文件')}</button>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}
