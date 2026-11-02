import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BadgeCheck, Check, Download, FolderOpen, PackagePlus, Pencil, Plus, RefreshCw, Search, ShieldAlert,
  Sparkles, Trash2, TriangleAlert, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useRoute } from '@/app/router'
import { CodeEditor } from '@/components/CodeEditor'
import { Markdown } from '@/components/Markdown'
import { Badge, Button, Card, Dialog, Empty, Field, Input, PageHeader, SectionTitle, Segmented, Select, Spinner, Switch,
  TabPanel, Tabs, Tip } from '@/components/ui'
import { confirmAction } from '@/components/confirm'
import { useContextMenu } from '@/components/ContextMenu'
import { del, errorMessage, get, post, put } from '@/lib/api'
import { useEvents } from '@/lib/events'
import { useCurrentProject } from '@/lib/store'
import type { CatalogEntry, Project, SkillItem } from '@/lib/types'
import { cn, formatTime, toast } from '@/lib/utils'

interface SkillsResponse { skills: SkillItem[]; errors: string[] }
interface SkillDetailData {
  name: string; description: string; body: string; meta: Record<string, any>; resources: string[]; trust: string
  status: string; root: string; path: string; lock: Record<string, any> | null; warnings: string[]
}
interface ScanFinding { severity: 'high' | 'medium' | 'low'; rule: string; file: string; line: number | null; message: string; excerpt: string }
interface InstallResult {
  name?: string; path?: string; status: 'installed' | 'ok' | 'refused' | 'invalid' | 'error'; reason?: string
  license?: string | null; license_status?: string; risk?: string; scan?: { risk: string }; description?: string
}
interface CatalogInfo {
  sources: { id: string; repo: string; path: string; license: string; trust: string; domains: string[]; description: string; note: string; user_added: boolean }[]
  packs: Record<string, { source: string; skills: string[]; tags: string[] }[]>
  index: { built_at: number | null; count: number; sources: Record<string, { count?: number; error?: string; commit?: string }> }
}
interface Domain { id: string; label: string; description: string; builtin?: boolean; source?: string }

const ROOT_LABELS: Record<string, string> = {
  project: '项目安装', learned: '自沉淀（已批准）', global: '全局安装', user: '用户目录 ~/.agents/skills', compat: '项目兼容目录', builtin: '内置',
}
const ROOT_ORDER = ['project', 'learned', 'global', 'user', 'compat', 'builtin']
const TRUST: Record<string, { label: string; tone: 'success' | 'warning' | 'info' | 'accent' | 'neutral' }> = {
  builtin: { label: '内置', tone: 'info' }, reviewed: { label: '已审核', tone: 'success' },
  unreviewed: { label: '未审核', tone: 'warning' }, learned: { label: '自沉淀', tone: 'accent' },
}
const RISK_TONE: Record<string, 'success' | 'warning' | 'danger'> = { low: 'success', medium: 'warning', high: 'danger' }

function scopeOf(root: string): 'project' | 'global' | null {
  return root === 'project' ? 'project' : root === 'global' ? 'global' : null
}

function summarizeInstall(results: InstallResult[], t: (s: string, o?: any) => string): void {
  const ok = results.filter((r) => r.status === 'installed')
  const bad = results.filter((r) => r.status !== 'installed' && r.status !== 'ok')
  if (ok.length) toast.success(t('已安装 {{n}} 个技能：{{names}}', { n: ok.length, names: ok.map((r) => r.name).join('、') }))
  for (const r of bad.slice(0, 3)) toast.error(`${r.name ?? r.path ?? ''}：${r.reason ?? r.status}`)
  if (!ok.length && !bad.length) toast.info(t('没有需要安装的技能'))
}

export default function SkillsPage() {
  const { t } = useTranslation()
  const route = useRoute()
  const project = useCurrentProject()
  const [tab, setTab] = useState(route.param ?? 'installed')
  const [installing, setInstalling] = useState(false)
  const [detail, setDetail] = useState<string | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['skills', project?.id ?? null],
    queryFn: () => get<SkillsResponse>('/skills', { project_id: project?.id }),
  })
  const pending = data?.skills.filter((s) => s.status === 'pending') ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={t('技能')}
        description={t('遵循 Agent Skills 开放标准（SKILL.md）。Agent 平时只看到技能清单，需要时再激活加载全文；外部技能安装前会检查许可证并做安全扫描。')}
        actions={<Button variant="primary" onClick={() => setInstalling(true)}><Download className="h-4 w-4" />{t('从 GitHub / 本地安装')}</Button>}
      />
      <Tabs value={tab} onValueChange={setTab} className="flex-1" tabs={[
        { value: 'installed', label: <>{t('已安装')} <span className="text-subtle">{data?.skills.filter((s) => s.status === 'active').length ?? ''}</span></> },
        { value: 'discover', label: t('发现') },
        { value: 'pending', label: <>{t('待批准')} {pending.length > 0 && <Badge tone="warning">{pending.length}</Badge>}</> },
        { value: 'domains', label: t('领域包') },
      ]}>
        <TabPanel value="installed" className="px-6 py-5">
          {isLoading ? <Spinner /> : <Installed data={data} project={project} onOpen={setDetail} />}
        </TabPanel>
        <TabPanel value="discover" className="px-6 py-5"><Discover project={project} /></TabPanel>
        <TabPanel value="pending" className="px-6 py-5"><Pending items={pending} project={project} onOpen={setDetail} /></TabPanel>
        <TabPanel value="domains" className="px-6 py-5"><Domains /></TabPanel>
      </Tabs>
      {installing && <InstallDialog project={project} onClose={() => setInstalling(false)} />}
      {detail && <SkillDetail name={detail} project={project} onClose={() => setDetail(null)} />}
    </div>
  )
}

// ---- installed ------------------------------------------------------------------------------------

function Installed({ data, project, onOpen }: { data?: SkillsResponse; project: Project | null; onOpen: (name: string) => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [root, setRoot] = useState('all')
  const active = (data?.skills ?? []).filter((s) => s.status === 'active')
  const filtered = active.filter((s) => (root === 'all' || s.root === root) &&
    (!q || `${s.name} ${s.description} ${s.tags.join(' ')}`.toLowerCase().includes(q.toLowerCase())))
  const groups = ROOT_ORDER.map((r) => [r, filtered.filter((s) => s.root === r)] as const).filter(([, items]) => items.length)

  return (
    <div className="flex max-w-6xl flex-col gap-5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-72">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <Input className="pl-8" placeholder={t('按名称、描述或标签筛选')} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Select className="w-auto" value={root} onChange={(e) => setRoot(e.target.value)}>
          <option value="all">{t('全部来源')}</option>
          {ROOT_ORDER.map((r) => <option key={r} value={r}>{t(ROOT_LABELS[r])}</option>)}
        </Select>
        <span className="text-xs text-muted">{project ? t('包含项目「{{p}}」中的技能', { p: project.name }) : t('未选择项目：只显示全局与内置技能')}</span>
      </div>
      {groups.map(([r, items]) => (
        <div key={r}>
          <SectionTitle>{t(ROOT_LABELS[r])} · {items.length}</SectionTitle>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {items.map((s) => <SkillCard key={s.name} skill={s} onClick={() => onOpen(s.name)} onRemoved={() => void qc.invalidateQueries({ queryKey: ['skills'] })} />)}
          </div>
        </div>
      ))}
      {!groups.length && <Empty icon={<Sparkles className="h-8 w-8" />} title={t('没有匹配的技能')} hint={t('到“发现”页检索并安装外部技能，或让 Agent 在研究结束后沉淀技能。')} />}
      {!!data?.errors.length && (
        <Card className="border-warning/40 p-3">
          <SectionTitle>{t('无法解析的技能目录')}</SectionTitle>
          {data.errors.map((e) => <div key={e} className="font-mono text-[11px] text-muted">{e}</div>)}
        </Card>
      )}
    </div>
  )
}

function SkillCard({ skill, onClick, onRemoved }: { skill: SkillItem; onClick: () => void; onRemoved?: () => void }) {
  const { t } = useTranslation()
  const trust = TRUST[skill.trust] ?? TRUST.unreviewed
  const risk = skill.lock?.scan?.risk as string | undefined
  // Only an installed skill can be removed; builtin and workspace ones come from files we do not own.
  const removable = skill.root === 'global' || skill.root === 'project'
  const menu = useContextMenu([
    { label: t('查看详情'), onSelect: onClick },
    { label: t('删除技能'), icon: <Trash2 className="h-3.5 w-3.5" />, danger: true, separatorBefore: true,
      disabled: !removable,
      onSelect: async () => {
        if (!(await confirmAction({ title: t('删除技能 {{n}}？', { n: skill.name }), danger: true }))) return
        try {
          await post(`/skills/remove/${encodeURIComponent(skill.name)}`, { scope: skill.root })
          onRemoved?.()
          toast.success(t('已删除'))
        } catch (err) {
          toast.error(errorMessage(err))
        }
      } },
  ])
  return (
    <>
    <button {...menu.trigger} onClick={onClick} className="flex flex-col gap-1.5 rounded-lg border border-border bg-surface p-3 text-left transition hover:border-primary/60">
      <div className="flex items-center gap-1.5">
        <Sparkles className="h-3.5 w-3.5 shrink-0 text-accent" />
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] font-medium">{skill.name}</span>
        {!!skill.warnings.length && (
          <Tip content={skill.warnings.join('\n')}><TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" /></Tip>
        )}
      </div>
      <p className="line-clamp-2 text-xs leading-relaxed text-muted">{skill.description}</p>
      <div className="mt-auto flex flex-wrap gap-1">
        <Badge tone={trust.tone}>{t(trust.label)}</Badge>
        {risk && risk !== 'low' && <Badge tone={RISK_TONE[risk]}>{t('风险')} {risk}</Badge>}
        {skill.license && <Badge>{skill.license}</Badge>}
        {skill.tags.slice(0, 3).map((tag) => <Badge key={tag} tone="primary">{tag}</Badge>)}
        {!!skill.shadowed.length && <Tip content={skill.shadowed.join('\n')}><span><Badge>{t('覆盖了 {{n}} 个同名技能', { n: skill.shadowed.length })}</Badge></span></Tip>}
      </div>
    </button>
    {menu.menu}
    </>
  )
}

// ---- detail -----------------------------------------------------------------------------------------

function SkillDetail({ name, project, onClose }: { name: string; project: Project | null; onClose: () => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { data, error } = useQuery({
    queryKey: ['skill-detail', name, project?.id ?? null],
    queryFn: () => get<SkillDetailData>(`/skills/detail/${encodeURIComponent(name)}`, { project_id: project?.id }),
  })
  const [tab, setTab] = useState('doc')
  const [scan, setScan] = useState<{ risk: string; findings: ScanFinding[]; files: number } | null>(null)
  const [update, setUpdate] = useState<{ available: boolean; changed?: string[]; commit?: string; old_commit?: string; reason?: string; scan?: { risk: string } } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const scope = data ? scopeOf(data.root) : null
  const body = { scope, project_id: project?.id }

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key)
    try {
      await fn()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ['skills'] }), qc.invalidateQueries({ queryKey: ['skill-detail'] })])

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-4xl"
      title={<span className="font-mono">{name}</span>} description={data?.description}
      footer={data && (
        <>
          {data.lock && scope && (
            <>
              <Button variant="ghost" className="mr-auto text-danger" loading={busy === 'remove'} onClick={() => run('remove', async () => {
                if (!(await confirmAction({ title: t('删除技能 {{n}}？', { n: name }), danger: true }))) return
                await post(`/skills/remove/${encodeURIComponent(name)}`, body)
                await refresh()
                onClose()
              })}><Trash2 className="h-4 w-4" />{t('删除')}</Button>
              <Button variant="outline" loading={busy === 'update'} onClick={() => run('update', async () => {
                setUpdate(await post(`/skills/update-check/${encodeURIComponent(name)}`, body))
                setTab('update')
              })}><RefreshCw className="h-4 w-4" />{t('检查更新')}</Button>
              <Button variant="outline" loading={busy === 'trust'} onClick={() => run('trust', async () => {
                await post(`/skills/trust/${encodeURIComponent(name)}`, { ...body, reviewed: data.trust !== 'reviewed' })
                await refresh()
              })}><BadgeCheck className="h-4 w-4" />{data.trust === 'reviewed' ? t('取消审核标记') : t('标记为已审核')}</Button>
            </>
          )}
          <Button variant="outline" loading={busy === 'scan'} onClick={() => run('scan', async () => {
            setScan(await post(`/skills/scan/${encodeURIComponent(name)}`, {}, { project_id: project?.id }))
            setTab('scan')
          })}><ShieldAlert className="h-4 w-4" />{t('安全扫描')}</Button>
        </>
      )}>
      {error ? <Empty title={t('读取失败')} hint={errorMessage(error)} /> : !data ? <Spinner /> : (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs md:grid-cols-3">
            <Info label={t('来源')} value={t(ROOT_LABELS[data.root] ?? data.root)} />
            <Info label={t('信任')} value={t(TRUST[data.trust]?.label ?? data.trust)} />
            <Info label={t('许可证')} value={data.lock?.license ?? data.meta.license ?? '—'} />
            {data.lock && <Info label={t('安装自')} value={data.lock.source} mono />}
            {data.lock?.commit && <Info label="commit" value={String(data.lock.commit).slice(0, 12)} mono />}
            {data.lock && <Info label={t('安装时间')} value={formatTime(data.lock.installed_at)} />}
            {data.meta.compatibility && <Info label={t('兼容性')} value={data.meta.compatibility} />}
            {data.meta.allowed_tools && <Info label="allowed-tools" value={data.meta.allowed_tools} mono />}
            <div className="col-span-full flex items-center gap-2">
              <span className="text-subtle">{t('目录')}</span>
              <span className="min-w-0 flex-1 truncate font-mono text-muted">{data.path}</span>
              {window.a4s && <Button size="sm" variant="ghost" onClick={() => void window.a4s?.openPath(data.path)}><FolderOpen className="h-3.5 w-3.5" /></Button>}
            </div>
          </div>
          {!!data.warnings.length && (
            <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs">
              {data.warnings.map((w) => (
                <div key={w} className="flex items-start gap-1.5">
                  <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0 text-warning" />{w}
                </div>
              ))}
            </div>
          )}
          <Tabs value={tab} onValueChange={setTab} className="-mx-5 min-h-[260px]" tabs={[
            { value: 'doc', label: 'SKILL.md' },
            { value: 'files', label: `${t('资源文件')} (${data.resources.length})` },
            ...(scan ? [{ value: 'scan', label: t('扫描结果') }] : []),
            ...(update ? [{ value: 'update', label: t('更新') }] : []),
          ]}>
            <TabPanel value="doc" className="px-5 pt-3"><Markdown text={data.body} /></TabPanel>
            <TabPanel value="files" className="px-5 pt-3">
              {data.resources.length ? data.resources.map((r) => <div key={r} className="font-mono text-xs leading-6">{r}</div>)
                : <span className="text-xs text-subtle">{t('只有 SKILL.md')}</span>}
            </TabPanel>
            {scan && (
              <TabPanel value="scan" className="px-5 pt-3">
                <div className="mb-3 flex items-center gap-2 text-sm">{t('风险等级')} <Badge tone={RISK_TONE[scan.risk]}>{scan.risk}</Badge>
                  <span className="text-xs text-muted">{t('检查了 {{n}} 个文件', { n: scan.files })}</span></div>
                {scan.findings.map((f, i) => (
                  <div key={i} className="mb-2 rounded-md border border-border p-2 text-xs">
                    <div className="flex items-center gap-2"><Badge tone={RISK_TONE[f.severity]}>{f.severity}</Badge>
                      <span className="font-medium">{f.message}</span><span className="ml-auto font-mono text-subtle">{f.file}{f.line ? `:${f.line}` : ''}</span></div>
                    {f.excerpt && <pre className="mt-1.5 overflow-x-auto rounded bg-code p-1.5 font-mono text-[11px]">{f.excerpt}</pre>}
                  </div>
                ))}
                {!scan.findings.length && <div className="text-xs text-success">{t('没有发现问题')}</div>}
              </TabPanel>
            )}
            {update && (
              <TabPanel value="update" className="px-5 pt-3 text-sm">
                {!update.available ? <div className="text-muted">{update.reason ?? t('已是最新版本')}</div> : (
                  <div className="flex flex-col gap-3">
                    <div>{t('上游有更新')}{(update.old_commit || update.commit) && <span className="ml-1 font-mono text-xs">{String(update.old_commit ?? '?').slice(0, 8)} → {String(update.commit ?? '?').slice(0, 8)}</span>}
                      {update.scan && <Badge className="ml-2" tone={RISK_TONE[update.scan.risk]}>{t('新版本风险')} {update.scan.risk}</Badge>}</div>
                    <div className="rounded-md border border-border p-2">
                      <div className="mb-1 text-xs text-muted">{t('变更的文件')}</div>
                      {update.changed?.map((f) => <div key={f} className="font-mono text-xs">{f}</div>)}
                    </div>
                    <Button variant="primary" className="self-start" loading={busy === 'apply'} onClick={() => run('apply', async () => {
                      const src = data.lock!.source as string
                      summarizeInstall(await post<InstallResult[]>('/skills/install', { source: src, select: [name], ...body }), t)
                      setUpdate(null)
                      setTab('doc')
                      await refresh()
                    })}>{t('更新并重新扫描')}</Button>
                  </div>
                )}
              </TabPanel>
            )}
          </Tabs>
        </div>
      )}
    </Dialog>
  )
}

function Info({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return <div className="flex min-w-0 gap-2"><span className="shrink-0 text-subtle">{label}</span><span className={cn('min-w-0 truncate', mono && 'font-mono')}>{value}</span></div>
}

// ---- install dialog ------------------------------------------------------------------------------------

function InstallDialog({ project, onClose, initial }: { project: Project | null; onClose: () => void; initial?: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [source, setSource] = useState(initial ?? '')
  const [scope, setScope] = useState<'global' | 'project'>(project ? 'project' : 'global')
  const [allowLicense, setAllowLicense] = useState(false)
  const [force, setForce] = useState(false)
  const [preview, setPreview] = useState<InstallResult[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const req = { source: source.trim(), scope, project_id: scope === 'project' ? project?.id : undefined, allow_license: allowLicense, force }

  const doPreview = async () => {
    setBusy(true)
    try {
      const res = await post<InstallResult[]>('/skills/install', { ...req, dry_run: true })
      setPreview(res)
      setSelected(new Set(res.filter((r) => r.status === 'ok' && r.name).map((r) => r.name!)))
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  const doInstall = async () => {
    setBusy(true)
    try {
      summarizeInstall(await post<InstallResult[]>('/skills/install', { ...req, select: [...selected] }), t)
      await qc.invalidateQueries({ queryKey: ['skills'] })
      onClose()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={t('安装技能')} width="max-w-2xl"
      description={t('安装期间不会执行技能里的任何代码；来源固定到具体 commit，记录在 skills.lock。')}
      footer={<>
        <Button variant="ghost" onClick={onClose}>{t('取消')}</Button>
        {preview ? <Button variant="primary" disabled={!selected.size} loading={busy} onClick={doInstall}>{t('安装所选（{{n}}）', { n: selected.size })}</Button>
          : <Button variant="primary" disabled={!source.trim()} loading={busy} onClick={doPreview}>{t('读取并检查')}</Button>}
      </>}>
      <div className="flex flex-col gap-4">
        <Field label={t('来源')} hint={t('例：K-Dense-AI/scientific-agent-skills/skills/neurokit2、GitHub 目录链接、owner/repo@v1.2、任意 git 地址或本地文件夹')}>
          <Input autoFocus value={source} placeholder="owner/repo[/path][@ref]" onChange={(e) => { setSource(e.target.value); setPreview(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter' && source.trim() && !preview) void doPreview() }} />
        </Field>
        <div className="flex flex-wrap items-center gap-4">
          <Segmented value={scope} onChange={(v) => { setScope(v); setPreview(null) }} options={[
            { value: 'global', label: t('全局（所有项目可用）') },
            ...(project ? [{ value: 'project' as const, label: t('当前项目「{{p}}」', { p: project.name }) }] : []),
          ]} />
          <Switch checked={allowLicense} onCheckedChange={(v) => { setAllowLicense(v); setPreview(null) }} label={<span className="text-xs">{t('允许非开源许可证')}</span>} />
          <Switch checked={force} onCheckedChange={(v) => { setForce(v); setPreview(null) }} label={<span className="text-xs text-danger">{t('忽略高风险扫描结果')}</span>} />
        </div>
        {preview && (
          <div className="flex flex-col gap-1.5">
            <SectionTitle actions={<span className="text-xs text-muted">{t('找到 {{n}} 个技能', { n: preview.length })}</span>}>{t('检查结果')}</SectionTitle>
            {preview.map((r, i) => {
              const ok = r.status === 'ok'
              return (
                <label key={`${r.path}-${i}`} className={cn('flex items-start gap-2.5 rounded-md border border-border p-2 text-xs', ok ? 'cursor-pointer hover:bg-surface-2' : 'opacity-70')}>
                  <input type="checkbox" className="mt-0.5" disabled={!ok || !r.name} checked={!!r.name && selected.has(r.name)} onChange={(e) => {
                    const next = new Set(selected)
                    if (e.target.checked) next.add(r.name!); else next.delete(r.name!)
                    setSelected(next)
                  }} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono font-medium">{r.name ?? r.path}</span>
                      {r.license !== undefined && <Badge tone={r.license_status === 'open' ? 'success' : 'warning'}>{r.license ?? t('无许可证')}</Badge>}
                      {r.scan && <Badge tone={RISK_TONE[r.scan.risk]}>{t('风险')} {r.scan.risk}</Badge>}
                      {!ok && <Badge tone="danger">{r.status}</Badge>}
                    </div>
                    {r.description && <div className="mt-0.5 line-clamp-2 text-muted">{r.description}</div>}
                    {r.reason && <div className="mt-0.5 text-danger">{r.reason}</div>}
                  </div>
                </label>
              )
            })}
          </div>
        )}
      </div>
    </Dialog>
  )
}

// ---- discover -------------------------------------------------------------------------------------------

function Discover({ project }: { project: Project | null }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('')
  const [scope, setScope] = useState<'global' | 'project'>(project ? 'project' : 'global')
  const [progress, setProgress] = useState<string[] | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)
  const [addSource, setAddSource] = useState(false)
  const { data: catalog } = useQuery({ queryKey: ['skill-catalog'], queryFn: () => get<CatalogInfo>('/skills/catalog') })
  const { data: domains } = useQuery({ queryKey: ['domains'], queryFn: () => get<Domain[]>('/domains') })
  const { data: results, isFetching } = useQuery({
    queryKey: ['skill-search', query, domain, project?.id ?? null],
    queryFn: () => get<CatalogEntry[]>('/skills/search', { q: query, domain, limit: 60, project_id: project?.id }),
    enabled: !!catalog?.index.count,
  })
  useEffect(() => {
    const id = setTimeout(() => setQuery(q), 250)
    return () => clearTimeout(id)
  }, [q])
  useEvents('skills', (e) => {
    if (e.type === 'catalog.started') setProgress([])
    if (e.type === 'catalog.progress') setProgress((p) => [...(p ?? []), `${e.data.source}：${e.data.status}`])
    if (e.type === 'catalog.done') {
      setProgress(null)
      toast.success(t('技能索引已更新'))
      void qc.invalidateQueries({ queryKey: ['skill-catalog'] })
      void qc.invalidateQueries({ queryKey: ['skill-search'] })
    }
  })
  const target = { scope, project_id: scope === 'project' ? project?.id : undefined }
  const install = async (key: string, fn: () => Promise<InstallResult[]>) => {
    setInstalling(key)
    try {
      summarizeInstall(await fn(), t)
      await qc.invalidateQueries({ queryKey: ['skills'] })
      await qc.invalidateQueries({ queryKey: ['skill-search'] })
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setInstalling(null)
    }
  }
  const sourceName = useMemo(() => Object.fromEntries((catalog?.sources ?? []).map((s) => [s.id, s.repo])), [catalog])

  return (
    <div className="flex max-w-6xl flex-col gap-6">
      <Card className="flex flex-wrap items-center gap-3 p-3 text-xs">
        <span className="text-muted">
          {catalog?.index.count ? t('索引包含 {{n}} 个技能，更新于 {{t}}', { n: catalog.index.count, t: formatTime(catalog.index.built_at) }) : t('技能索引尚未建立')}
        </span>
        <Button size="sm" variant="outline" loading={progress !== null} onClick={async () => {
          setProgress([])
          try {
            await post('/skills/catalog/refresh', {})
          } catch (err) {
            setProgress(null)
            toast.error(errorMessage(err))
          }
        }}><RefreshCw className="h-3.5 w-3.5" />{catalog?.index.count ? t('刷新索引') : t('联网建立索引')}</Button>
        <span className="ml-auto flex items-center gap-2 text-muted">{t('安装到')}
          <Segmented size="sm" value={scope} onChange={setScope} options={[
            { value: 'global', label: t('全局') }, ...(project ? [{ value: 'project' as const, label: project.name }] : []),
          ]} />
        </span>
        {progress && progress.length > 0 && (
          <div className="w-full font-mono text-[11px] text-muted">{progress.slice(-4).map((p, i) => <div key={i}>{p}</div>)}</div>
        )}
      </Card>

      <div>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="relative w-96 max-w-full">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
            <Input className="pl-8" placeholder={t('检索技能，如 eeg、single-cell、literature review')} value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <Select className="w-auto" value={domain} onChange={(e) => setDomain(e.target.value)}>
            <option value="">{t('全部领域')}</option>
            {domains?.map((d) => <option key={d.id} value={d.id}>{t(d.label)}</option>)}
          </Select>
          {isFetching && <Spinner />}
        </div>
        {!catalog?.index.count ? (
          <Empty icon={<Search className="h-8 w-8" />} title={t('先建立技能索引')}
            hint={t('索引会下载目录中各技能仓库的清单（只读取 SKILL.md 元数据，不执行代码），之后即可离线检索。')} />
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            {results?.map((r) => (
              <Card key={`${r.source}/${r.path}`} className="flex flex-col gap-1.5 p-3">
                <div className="flex items-center gap-1.5">
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px] font-medium">{r.name}</span>
                  {r.installed ? <Badge tone="success"><Check className="h-3 w-3" />{t('已安装')}</Badge> : (
                    <Button size="sm" variant="outline" loading={installing === r.spec}
                      onClick={() => install(r.spec, () => post<InstallResult[]>('/skills/install', { source: r.spec, select: [r.name], tags: r.domains, ...target }))}>
                      <Download className="h-3.5 w-3.5" />{t('安装')}
                    </Button>
                  )}
                </div>
                <p className="line-clamp-2 text-xs leading-relaxed text-muted">{r.description}</p>
                <div className="flex flex-wrap items-center gap-1">
                  <Badge tone={r.license_status === 'open' ? 'success' : 'warning'}>{r.license ?? t('许可证未知')}</Badge>
                  {r.trust === 'low' && <Tip content={t('来源声明内容由 AI 生成且未经专家审核')}><span><Badge tone="warning">{t('低信任')}</Badge></span></Tip>}
                  {r.domains.map((d) => <Badge key={d} tone="primary">{d}</Badge>)}
                  <span className="ml-auto truncate font-mono text-[10px] text-subtle">{sourceName[r.source] ?? r.source}</span>
                </div>
              </Card>
            ))}
            {results && !results.length && <div className="text-xs text-subtle">{t('没有找到匹配的技能')}</div>}
          </div>
        )}
      </div>

      <div>
        <SectionTitle>{t('领域技能包')}</SectionTitle>
        <div className="grid gap-2 md:grid-cols-2">
          {Object.entries(catalog?.packs ?? {}).map(([id, items]) => (
            <Card key={id} className="flex flex-col gap-2 p-3">
              <div className="flex items-center gap-2">
                <PackagePlus className="h-4 w-4 text-primary" />
                <span className="flex-1 text-sm font-medium">{t(domains?.find((d) => d.id === id)?.label ?? id)}</span>
                <Button size="sm" variant="outline" loading={installing === `pack:${id}`}
                  onClick={() => install(`pack:${id}`, () => post<InstallResult[]>(`/skills/packs/${id}/install`, target))}>
                  {t('安装整个技能包')}
                </Button>
              </div>
              {items.map((item) => (
                <div key={item.source} className="text-xs">
                  <span className="font-mono text-subtle">{sourceName[item.source] ?? item.source}</span>
                  <div className="mt-0.5 flex flex-wrap gap-1">{item.skills.map((s) => <Badge key={s}>{s}</Badge>)}</div>
                </div>
              ))}
            </Card>
          ))}
        </div>
      </div>

      <div>
        <SectionTitle actions={<Button size="sm" variant="ghost" onClick={() => setAddSource(true)}><Plus className="h-3.5 w-3.5" />{t('添加来源')}</Button>}>
          {t('技能来源')}
        </SectionTitle>
        <Card className="divide-y divide-border">
          {catalog?.sources.map((s) => {
            const info = catalog.index.sources[s.id]
            return (
              <div key={s.id} className="flex items-start gap-3 px-3 py-2 text-xs">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono font-medium">{s.path ? `${s.repo}/${s.path}` : s.repo}</span>
                    {s.license && <Badge>{s.license}</Badge>}
                    {s.trust === 'low' && <Badge tone="warning">{t('低信任')}</Badge>}
                    {s.user_added && <Badge tone="info">{t('自定义')}</Badge>}
                  </div>
                  <div className="mt-0.5 text-muted">{s.description}{s.note ? ` · ${s.note}` : ''}</div>
                </div>
                <span className="shrink-0 text-subtle">{info?.error ? <span className="text-danger">{t('索引失败')}</span> : info?.count !== undefined ? t('{{n}} 个技能', { n: info.count }) : ''}</span>
              </div>
            )
          })}
        </Card>
      </div>
      {addSource && <AddSourceDialog domains={domains ?? []} onClose={() => setAddSource(false)} />}
    </div>
  )
}

function AddSourceDialog({ domains, onClose }: { domains: Domain[]; onClose: () => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [repo, setRepo] = useState('')
  const [path, setPath] = useState('')
  const [description, setDescription] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={t('添加技能来源')}
      description={t('添加后刷新索引即可检索其中的技能。')}
      footer={<>
        <Button variant="ghost" onClick={onClose}>{t('取消')}</Button>
        <Button variant="primary" disabled={!/^[\w.-]+\/[\w.-]+$/.test(repo.trim())} onClick={async () => {
          try {
            await post('/skills/sources', { repo: repo.trim(), path: path.trim(), description, domains: chosen })
            await qc.invalidateQueries({ queryKey: ['skill-catalog'] })
            onClose()
          } catch (err) {
            toast.error(errorMessage(err))
          }
        }}>{t('添加')}</Button>
      </>}>
      <div className="flex flex-col gap-3">
        <Field label={t('GitHub 仓库')}><Input value={repo} placeholder="owner/repo" onChange={(e) => setRepo(e.target.value)} /></Field>
        <Field label={t('子目录（可空）')}><Input value={path} placeholder="skills" onChange={(e) => setPath(e.target.value)} /></Field>
        <Field label={t('说明')}><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label={t('领域')}>
          <div className="flex flex-wrap gap-1.5">
            {domains.map((d) => (
              <button key={d.id} onClick={() => setChosen(chosen.includes(d.id) ? chosen.filter((x) => x !== d.id) : [...chosen, d.id])}
                className={cn('rounded-md border px-2 py-1 text-xs', chosen.includes(d.id) ? 'border-primary bg-primary/10' : 'border-border')}>{t(d.label)}</button>
            ))}
          </div>
        </Field>
      </div>
    </Dialog>
  )
}

// ---- pending learned skills -------------------------------------------------------------------------------

function Pending({ items, project, onOpen }: { items: SkillItem[]; project: Project | null; onOpen: (name: string) => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  if (!project) return <Empty icon={<Sparkles className="h-8 w-8" />} title={t('请先选择项目')} hint={t('Agent 沉淀的技能保存在项目的 skills/learned 目录，批准后才会出现在技能清单中。')} />
  if (!items.length) return <Empty icon={<Sparkles className="h-8 w-8" />} title={t('没有待批准的技能')} hint={t('运行结束后，Agent 会从过程记录中提炼可复用的做法，提交到这里等待审批。')} />
  const review = async (name: string, approve: boolean) => {
    try {
      await post(`/skills/learned/${encodeURIComponent(name)}/review`, { project_id: project.id, approve })
      await qc.invalidateQueries({ queryKey: ['skills'] })
      toast.success(approve ? t('已启用技能 {{n}}', { n: name }) : t('已拒绝技能 {{n}}', { n: name }))
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }
  return (
    <div className="flex max-w-4xl flex-col gap-3">
      {items.map((s) => <PendingCard key={s.name} skill={s} project={project} onOpen={onOpen} onReview={review} />)}
    </div>
  )
}

function PendingCard({ skill, project, onOpen, onReview }: {
  skill: SkillItem; project: Project; onOpen: (name: string) => void; onReview: (name: string, approve: boolean) => Promise<void>
}) {
  const { t } = useTranslation()
  const { data } = useQuery({
    queryKey: ['skill-detail', skill.name, project.id],
    queryFn: () => get<SkillDetailData>(`/skills/detail/${encodeURIComponent(skill.name)}`, { project_id: project.id }),
  })
  return (
    <Card className="p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold">{skill.name}</span>
            <Badge tone="accent">{t('自沉淀')}</Badge>
            {!!skill.shadowed.length && <Badge tone="info">{t('更新已有技能')}</Badge>}
          </div>
          <p className="mt-1 text-xs text-muted">{skill.description}</p>
        </div>
        <Button size="sm" variant="ghost" onClick={() => onOpen(skill.name)}>{t('详情')}</Button>
        <Button size="sm" variant="outline" onClick={() => onReview(skill.name, false)}><X className="h-3.5 w-3.5" />{t('拒绝')}</Button>
        <Button size="sm" variant="primary" onClick={() => onReview(skill.name, true)}><Check className="h-3.5 w-3.5" />{t('批准启用')}</Button>
      </div>
      {data && <div className="mt-3 max-h-72 overflow-y-auto rounded-md border border-border bg-surface-2/40 px-4 py-2"><Markdown text={data.body} /></div>}
    </Card>
  )
}

// ---- domain packs ---------------------------------------------------------------------------------

const NEW_DOMAIN = `id: {{id}}
label: 我的领域
description: 一句话说明这个领域做什么
# 这个领域默认打开的工具组：fs shell python web literature research neuro memory skills human agent
tools: [literature, research]
# 「发现」页会按这些标签推荐技能
skill_tags: [general]
# 推荐的 MCP 服务器名（在 MCP 页添加后生效）
mcp: []
# 一键安装的技能（source 可以是精选目录的 id，也可以直接写 owner/repo[/path]）
skills:
  - source: k-dense
    skills: [literature-review, statistical-analysis]
    tags: [general]
# 附加到系统提示里的领域说明：方法学要求、数据与单位约定、常用工具链
prompt: |
  Domain notes: 在这里写这个领域的工作方式与硬性要求。
`

function Domains() {
  const { t } = useTranslation()
  const project = useCurrentProject()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['domains'], queryFn: () => get<Domain[]>('/domains') })
  const [editing, setEditing] = useState<{ id: string; source: string; isNew: boolean } | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['domains'] })

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <div className="flex items-start gap-3">
        <p className="flex-1 text-xs text-muted">
          {t('领域包决定新建项目时可选的研究领域：默认工具、推荐技能标签、推荐 MCP，以及写进系统提示的领域说明。内置四个（通用、AI/ML、生物化学材料、神经科学），你可以自己加，也可以覆盖内置的。')}
        </p>
        <Button variant="primary" size="sm" onClick={() => setEditing({ id: '', source: NEW_DOMAIN, isNew: true })}>
          <Plus className="h-3.5 w-3.5" />{t('新建领域包')}
        </Button>
      </div>
      {isLoading ? <Spinner /> : (
        <div className="flex flex-col gap-2">
          {data?.map((d) => (
            <DomainCard key={d.id} domain={d} project={project} onEdit={() => setEditing({ id: d.id, source: d.source ?? '', isNew: false })} onChanged={refresh} />
          ))}
        </div>
      )}
      {editing && (
        <DomainDialog initial={editing} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); await refresh() }} />
      )}
    </div>
  )
}

function DomainCard({ domain: d, project, onEdit, onChanged }: {
  domain: Domain
  project: Project | null
  onEdit: () => void
  onChanged: () => void | Promise<unknown>
}) {
  const { t } = useTranslation()

  const install = async () => {
    try {
      const res = await post<InstallResult[]>(`/skills/packs/${encodeURIComponent(d.id)}/install`,
        project ? { project_id: project.id, scope: 'project' } : { scope: 'global' })
      toast.success(t('已安装 {{n}} 个技能', { n: res.filter((r) => r.status !== 'error').length }))
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  const remove = async () => {
    if (!(await confirmAction({ title: t('删除领域包 {{id}}？', { id: d.id }),
      description: t('内置同名领域会恢复。'), danger: true }))) return
    try {
      await del(`/domains/${encodeURIComponent(d.id)}`)
      await onChanged()
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  const menu = useContextMenu([
    { label: d.builtin ? t('覆盖内置') : t('编辑'), icon: <Pencil className="h-3.5 w-3.5" />, onSelect: onEdit },
    { label: t('安装技能'), icon: <PackagePlus className="h-3.5 w-3.5" />, onSelect: () => void install() },
    { label: t('删除'), icon: <Trash2 className="h-3.5 w-3.5" />, danger: true, separatorBefore: true,
      disabled: d.builtin, onSelect: () => void remove() },
  ])

  return (
    <>
            <Card {...menu.trigger} className="flex items-center gap-3 p-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{t(d.label)}</span>
                  <Badge tone={d.builtin ? undefined : 'primary'}>{d.builtin ? t('内置') : t('自定义')}</Badge>
                  <span className="font-mono text-[11px] text-subtle">{d.id}</span>
                </div>
                {d.description && <div className="truncate text-xs text-muted">{t(d.description)}</div>}
              </div>
              <Tip content={t('安装这个领域推荐的技能（可在领域包的 skills: 里配置）')}>
                <Button size="sm" variant="outline" onClick={() => void install()}>
                  <PackagePlus className="h-3.5 w-3.5" />{t('安装技能')}
                </Button>
              </Tip>
              <Button size="sm" variant="outline" onClick={onEdit}>
                {d.builtin ? t('覆盖内置') : t('编辑')}
              </Button>
              {!d.builtin && (
                <Button size="icon" variant="ghost" className="text-danger" onClick={() => void remove()}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
            </Card>
      {menu.menu}
    </>
  )
}

function DomainDialog({ initial, onClose, onSaved }: {
  initial: { id: string; source: string; isNew: boolean }
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const [id, setId] = useState(initial.id)
  const [source, setSource] = useState(initial.source)
  const [busy, setBusy] = useState(false)
  const valid = /^[a-z0-9][a-z0-9_.-]*$/.test(id.trim())
  const save = async () => {
    setBusy(true)
    try {
      await put(`/domains/${encodeURIComponent(id.trim())}`, { source: source.replace(/\{\{id\}\}/g, id.trim()) })
      toast.success(t('已保存领域包'))
      onSaved()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-3xl"
      title={initial.isNew ? t('新建领域包') : t('编辑领域包 {{id}}', { id: initial.id })}
      description={t('YAML 格式。保存到数据目录的 domains/ 下，和内置领域包同名时会覆盖它。')}
      footer={<>
        <Button variant="outline" onClick={onClose}>{t('取消')}</Button>
        <Button variant="primary" loading={busy} disabled={!valid || !source.trim()} onClick={save}>{t('保存')}</Button>
      </>}>
      <div className="flex flex-col gap-3">
        {initial.isNew && (
          <Field label={t('名称（小写字母、数字、- 和 _）')}>
            <Input value={id} onChange={(e) => setId(e.target.value)} placeholder="game-design" />
          </Field>
        )}
        <CodeEditor value={source} onChange={setSource} language="yaml" height="420px" />
      </div>
    </Dialog>
  )
}
