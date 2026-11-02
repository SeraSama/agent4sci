import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Check, Download, ExternalLink, FolderOpen, Image, KeyRound, Minus, PanelLeft, PanelRight, Play,
  Trash2, Upload, Volume2, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { confirmAction } from '@/components/confirm'
import { useContextMenu } from '@/components/ContextMenu'
import { Marquee } from '@/components/Marquee'
import { Badge, Button, Card, Field, Input, PageHeader, SectionTitle, Segmented, Slider, Switch, TabPanel,
  Tabs } from '@/components/ui'
import { setLanguage } from '@/i18n'
import { assetUrl, del, errorMessage, get, post, put } from '@/lib/api'
import { playCue, type Cue } from '@/lib/sound'
import { useInfo } from '@/lib/store'
import { useUi } from '@/lib/uistore'
import { cn, formatBytes, formatTime, toast } from '@/lib/utils'
import { TermsTab } from '@/pages/Terms'
import { ThemeSettings } from '@/theme/ThemeEditor'

interface Settings {
  contact_email: string
  language: string
  demo_mode?: boolean
  memory_auto_apply: boolean
  services: Record<string, { label: string; env: string; configured: boolean }>
}

export default function SettingsPage() {
  const { t } = useTranslation()
  const [tab, setTab] = useState('appearance')
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={t('设置')} description={t('外观、语言、外部服务密钥与应用信息')} />
      <Tabs value={tab} onValueChange={setTab} className="flex-1" tabs={[
        { value: 'appearance', label: t('外观与主题') }, { value: 'general', label: t('通用') },
        { value: 'services', label: t('服务密钥') }, { value: 'terms', label: t('协议与声明') },
        { value: 'about', label: t('关于') },
      ]}>
        <TabPanel value="appearance" className="px-6 py-5">
          <div className="flex flex-col gap-7">
            <ThemeSettings />
            <Layout />
            <BackgroundSettings />
            <Sounds />
          </div>
        </TabPanel>
        <TabPanel value="general" className="px-6 py-5"><General /></TabPanel>
        <TabPanel value="services" className="px-6 py-5"><Services /></TabPanel>
        <TabPanel value="terms" className="px-6 py-5"><TermsTab /></TabPanel>
        <TabPanel value="about" className="px-6 py-5"><About /></TabPanel>
      </Tabs>
    </div>
  )
}

function useSettings() {
  return useQuery({ queryKey: ['settings'], queryFn: () => get<Settings>('/settings') })
}

function General() {
  const { t, i18n } = useTranslation()
  const { data } = useSettings()
  const qc = useQueryClient()
  const [email, setEmail] = useState<string | null>(null)
  if (!data) return null
  const save = async (patch: Partial<Settings>) => {
    try {
      await put('/settings', patch)
      await qc.invalidateQueries({ queryKey: ['settings'] })
      await qc.invalidateQueries({ queryKey: ['info'] })
      await qc.invalidateQueries({ queryKey: ['models'] })
      toast.success(i18n.t('已保存'))  // i18n.t, not t: a language switch must toast in the new language
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }
  return (
    <div className="flex max-w-2xl flex-col gap-6">
      <section>
        <SectionTitle>{t('语言')}</SectionTitle>
        <Segmented value={i18n.language.startsWith('en') ? 'en' : 'zh-CN'} onChange={(lang) => {
          setLanguage(lang)
          void save({ language: lang })
        }} options={[{ value: 'zh-CN', label: '中文' }, { value: 'en', label: 'English' }]} />
      </section>
      <section className="flex flex-col gap-3">
        <SectionTitle>{t('文献检索')}</SectionTitle>
        <Field label={t('联系邮箱（Crossref / PubMed 的礼貌访问池需要）')}
          hint={t('只会发给 Crossref 与 NCBI 的 API，用于标识请求来源；留空则使用公共限速。')}>
          <div className="flex gap-2">
            <Input value={email ?? data.contact_email} onChange={(e) => setEmail(e.target.value)} placeholder="name@lab.org" />
            <Button onClick={() => void save({ contact_email: email ?? data.contact_email })}>{t('保存')}</Button>
          </div>
        </Field>
      </section>
      <section className="flex flex-col gap-3">
        <SectionTitle>{t('体验与记忆')}</SectionTitle>
        <Switch checked={!!data.demo_mode} onCheckedChange={(v) => void save({ demo_mode: v })}
          label={<span>{t('启用演示模型')}<span className="ml-2 text-xs text-muted">{t('无需 API Key，用预设脚本演示完整界面与流程')}</span></span>} />
        <Switch checked={data.memory_auto_apply} onCheckedChange={(v) => void save({ memory_auto_apply: v })}
          label={<span>{t('自动接受记忆更新提议')}<span className="ml-2 text-xs text-muted">{t('关闭时需在对话中手动确认')}</span></span>} />
      </section>
    </div>
  )
}

function Services() {
  const { t } = useTranslation()
  const { data } = useSettings()
  const qc = useQueryClient()
  const [values, setValues] = useState<Record<string, string>>({})
  if (!data) return null
  const links: Record<string, string> = {
    s2: 'https://www.semanticscholar.org/product/api#api-key', openalex: 'https://openalex.org/settings/api',
    ncbi: 'https://www.ncbi.nlm.nih.gov/account/settings/', tavily: 'https://tavily.com', brave: 'https://brave.com/search/api/',
    github: 'https://github.com/settings/tokens',
  }
  const notes: Record<string, string> = {
    s2: '未配置时 Semantic Scholar 经常限流（HTTP 429）。', openalex: '2026 年起 OpenAlex 需要 API Key，并按量计费（每日有免费额度）。',
    ncbi: '有 Key 时 PubMed 限速从 3 次/秒提高到 10 次/秒。', tavily: '为非 Claude 模型提供网页搜索。', brave: '为非 Claude 模型提供网页搜索（二选一）。',
    github: '可选：提高从 GitHub 下载技能时的 API 限额。',
  }
  return (
    <div className="flex max-w-3xl flex-col gap-3">
      <p className="text-xs text-muted">{t('密钥保存在系统钥匙串（macOS 钥匙串 / Windows 凭据管理器）中，不会写入数据库或日志。')}</p>
      {Object.entries(data.services).map(([id, svc]) => (
        <Card key={id} className="flex items-center gap-4 px-4 py-3">
          <KeyRound className="h-4 w-4 shrink-0 text-muted" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm font-medium">{t(svc.label)}
              {svc.configured ? <Badge tone="success"><Check className="h-3 w-3" />{t('已配置')}</Badge> : <Badge>{t('未配置')}</Badge>}
            </div>
            <div className="mt-0.5 text-xs text-muted">{t(notes[id] ?? '')} <span className="font-mono">{svc.env}</span></div>
          </div>
          <Input type="password" className="w-56" placeholder={svc.configured ? '••••••••' : t('粘贴 API Key')}
            value={values[id] ?? ''} onChange={(e) => setValues({ ...values, [id]: e.target.value })} />
          <Button size="sm" variant="primary" onClick={async () => {
            await put(`/settings/services/${id}`, { value: values[id] ?? '' })
            setValues({ ...values, [id]: '' })
            await qc.invalidateQueries({ queryKey: ['settings'] })
            toast.success(values[id] ? t('已保存') : t('已清除'))
          }}>{values[id] ? t('保存') : t('清除')}</Button>
          <Button size="icon" variant="ghost" onClick={() => window.a4s ? window.a4s.openExternal(links[id]) : window.open(links[id])}>
            <ExternalLink className="h-3.5 w-3.5" />
          </Button>
        </Card>
      ))}
    </div>
  )
}

function About() {
  const { t } = useTranslation()
  const { data } = useInfo()
  if (!data) return null
  const rows: [string, string][] = [
    [t('版本'), data.version], [t('平台'), data.platform], [t('数据目录'), data.data_dir],
    [t('默认项目目录'), data.projects_dir], [t('密钥存储'), data.secret_store],
  ]
  return (
    <div className="flex max-w-2xl flex-col gap-4">
      <Card className="divide-y divide-border">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[minmax(4rem,7rem)_minmax(0,1fr)] items-baseline gap-4 px-4 py-2.5 text-sm">
            <Marquee className="text-muted" title={k}>{k}</Marquee>
            <span className="min-w-0 font-mono text-xs break-all">{v}</span>
          </div>
        ))}
      </Card>
      {window.a4s && (
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => window.a4s?.openPath(data.data_dir)}><FolderOpen className="h-4 w-4" />{t('打开数据目录')}</Button>
        </div>
      )}
      <Backups />
      <ConfigBundle />
      <Diagnostics />
      <p className="text-xs leading-relaxed text-muted">
        {t('Agent4Sci 是一个面向自动化科研的 Agent 框架：多模型适配、Agent Skills、MCP、分层记忆、可视化 Harness 与 Loop Engineering。')}
      </p>
    </div>
  )
}

interface BackupFile { path: string; name: string; size: number; created: number }

/** Back up everything the app keeps (database, memories, skills, field packs) into one zip. */
function Backups() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const { data } = useQuery({
    queryKey: ['backups'],
    queryFn: () => get<{ backups: BackupFile[]; dir: string; data: { total: number; parts: Record<string, number> } }>('/backups'),
  })
  const refresh = () => qc.invalidateQueries({ queryKey: ['backups'] })

  const make = async () => {
    setBusy('new')
    try {
      const made = await post<{ path: string; files: number; models_needing_keys: string[] }>('/backups', {})
      await refresh()
      toast.success(t('已备份 {{n}} 个文件', { n: made.files }))
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const restore = async (file: BackupFile) => {
    if (!(await confirmAction({ title: t('用「{{n}}」覆盖当前数据？', { n: file.name }),
      description: t('当前数据会先自动备份一份。'), danger: true, confirmLabel: t('恢复') }))) return
    setBusy(file.path)
    try {
      const done = await post<{ models_needing_keys: string[]; safety_backup: string }>('/backups/restore', { path: file.path })
      const keys = done.models_needing_keys ?? []
      toast.success(keys.length
        ? t('已恢复。这些模型需要重新填 API Key：{{m}}。请重启应用。', { m: keys.join('、') })
        : t('已恢复，请重启应用。'))
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(null)
      await refresh()
    }
  }

  return (
    <section>
      <SectionTitle>{t('备份与恢复')}</SectionTitle>
      <p className="mb-2 text-xs text-muted">
        {t('备份对话、运行记录、模型档案、Harness、Loop、记忆、技能与领域包。项目文件夹不在内（它们有自己的 git 历史），背景图片与视频也不在内（太大）。API Key 存在系统钥匙串里，不会进备份——恢复后需要重新填。')}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" loading={busy === 'new'} onClick={make}><Archive className="h-4 w-4" />{t('立即备份')}</Button>
        {data?.data && <span className="text-xs text-muted">{t('数据目录约 {{s}}', { s: formatBytes(data.data.total) })}</span>}
        {window.a4s && data?.dir && (
          <button className="text-xs text-primary underline" onClick={() => window.a4s?.openPath(data.dir)}>{t('打开备份目录')}</button>
        )}
      </div>
      {data?.backups?.length ? (
        <div className="mt-2 flex flex-col gap-1">
          {data.backups.slice(0, 6).map((file) => (
            <div key={file.path} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border px-3 py-1.5 text-xs">
              <Marquee className="min-w-[8rem] flex-1 font-mono" title={file.name}>{file.name}</Marquee>
              <span className="shrink-0 text-subtle">{formatBytes(file.size)}</span>
              <span className="shrink-0 text-subtle">{formatTime(file.created)}</span>
              <Button size="sm" variant="outline" loading={busy === file.path} onClick={() => restore(file)}>{t('恢复')}</Button>
            </div>
          ))}
        </div>
      ) : <div className="mt-2 text-xs text-subtle">{t('还没有备份')}</div>}
    </section>
  )
}

/** Everything another machine needs to reproduce this setup — except the API keys. */
function ConfigBundle() {
  const { t } = useTranslation()
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<{ added: string[]; replaced: string[]; skipped: string[]; models_needing_keys: string[] } | null>(null)

  const save = async () => {
    setBusy(true)
    try {
      const bundle = await get<Record<string, unknown>>('/config/export')
      const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `agent4sci-config-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const load = async (file: File, overwrite: boolean) => {
    setBusy(true)
    try {
      const bundle = JSON.parse(await file.text())
      const result = await post<typeof report & object>('/config/import', { bundle, overwrite })
      setReport(result as any)
      toast.success(t('已导入：新增 {{a}} · 覆盖 {{r}} · 跳过 {{s}}', {
        a: result!.added.length, r: result!.replaced.length, s: result!.skipped.length }))
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <SectionTitle>{t('配置包')}</SectionTitle>
      <p className="mb-2 text-xs text-muted">
        {t('导出模型档案、Harness、Loop、领域包、MCP 配置和角色映射，拿到另一台机器或给同事导入。不含 API Key。')}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" loading={busy} onClick={save}><Download className="h-4 w-4" />{t('导出配置')}</Button>
        <Button variant="outline" loading={busy} onClick={() => input.current?.click()}><Upload className="h-4 w-4" />{t('导入配置')}</Button>
        <span className="text-xs text-subtle">{t('同名项默认跳过；按住 Shift 点"导入配置"则覆盖')}</span>
        <input ref={input} type="file" accept=".json" className="hidden" onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) void load(file, (window.event as MouseEvent | undefined)?.shiftKey ?? false)
          e.target.value = ''
        }} />
      </div>
      {report && (
        <div className="mt-2 flex flex-col gap-1 text-xs text-muted">
          {(['added', 'replaced', 'skipped'] as const).map((k) => report[k].length > 0 && (
            <div key={k}>
              <span className="text-subtle">{t(k === 'added' ? '新增' : k === 'replaced' ? '覆盖' : '跳过')}：</span>
              <span className="font-mono">{report[k].join('、')}</span>
            </div>
          ))}
          {report.models_needing_keys.length > 0 && (
            <div className="text-warning">{t('这些模型需要重新填 API Key：{{m}}', { m: report.models_needing_keys.join('、') })}</div>
          )}
        </div>
      )}
    </section>
  )
}

/** What `a4s doctor` checks — the packaged app has no CLI, so this is the only self-check available. */
function Diagnostics() {
  const { t } = useTranslation()
  const [network, setNetwork] = useState(false)
  const { data, isFetching, refetch } = useQuery({
    queryKey: ['diagnostics', network],
    queryFn: () => get<{ checks: { name: string; ok: boolean | null; detail: string; hint: string }[]; failed: number }>(
      '/diagnostics', network ? { network: 'true' } : undefined),
  })
  return (
    <section>
      <SectionTitle>{t('环境诊断')}</SectionTitle>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" loading={isFetching} onClick={() => void refetch()}>{t('重新检查')}</Button>
        <Switch checked={network} onCheckedChange={setNetwork} label={<span className="text-xs">{t('同时检查网络（arXiv / Crossref / GitHub）')}</span>} />
        {data && (data.failed > 0
          ? <Badge tone="danger">{t('{{n}} 项有问题', { n: data.failed })}</Badge>
          : <Badge tone="success">{t('一切正常')}</Badge>)}
      </div>
      <Card className="divide-y divide-border">
        {data?.checks.map((c) => (
          <div key={c.name} className="grid grid-cols-[1rem_minmax(5rem,9rem)_minmax(0,1fr)] items-baseline gap-x-3 px-3 py-1.5 text-xs">
            <StatusMark ok={c.ok} />
            <Marquee title={c.name}>{c.name}</Marquee>
            <span className="min-w-0 font-mono break-all">{c.detail}</span>
            {c.hint && <span className="col-start-3 mt-0.5 leading-relaxed text-warning">{c.hint}</span>}
          </div>
        ))}
      </Card>
    </section>
  )
}

/** Where the sidebar sits. Stored with the other appearance prefs, so it rides along in a backup. */
/** A diagnostic's verdict, drawn rather than typed. */
function StatusMark({ ok }: { ok: boolean | null }) {
  if (ok === null) return <Minus className="h-3 w-3 translate-y-0.5 text-subtle" />
  return ok
    ? <Check className="h-3 w-3 translate-y-0.5 text-success" />
    : <X className="h-3 w-3 translate-y-0.5 text-danger" />
}

function Layout() {
  const { t } = useTranslation()
  const side = useUi((s) => s.ui.sidebarSide)
  const setUi = useUi((s) => s.set)
  return (
    <section className="max-w-2xl">
      <SectionTitle>{t('布局')}</SectionTitle>
      <Field label={t('侧边栏位置')} hint={window.a4s?.platform === 'darwin'
        ? t('放到右边时，窗口左上角的红绿灯会占用主区域顶部的一条拖动区。')
        : undefined}>
        <Segmented value={side} onChange={(v) => void setUi({ sidebarSide: v })} options={[
          { value: 'left' as const, label: <span className="flex items-center gap-1.5"><PanelLeft className="h-3.5 w-3.5" />{t('靠左')}</span> },
          { value: 'right' as const, label: <span className="flex items-center gap-1.5"><PanelRight className="h-3.5 w-3.5" />{t('靠右')}</span> },
        ]} />
      </Field>
    </section>
  )
}

interface Asset { id: string; name: string; kind: 'image' | 'video'; size: number; created: number; heavy?: boolean }

/** Background image or looping video, with the knobs that keep it readable. */
function BackgroundSettings() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const bg = useUi((s) => s.ui.background)
  const setBg = useUi((s) => s.setBackground)
  const [busy, setBusy] = useState(false)
  const [dropping, setDropping] = useState(false)
  const { data: assets } = useQuery({ queryKey: ['assets'], queryFn: () => get<Asset[]>('/assets') })
  const current = assets?.find((a) => a.id === bg.asset)

  const add = async (path: string) => {
    setBusy(true)
    try {
      const asset = await post<Asset>('/assets', { path })
      await qc.invalidateQueries({ queryKey: ['assets'] })
      await setBg({ asset: asset.id, kind: asset.kind })
      if (asset.heavy) toast.info(t('这段视频有 {{s}}，循环播放会明显吃电和 GPU。', { s: formatBytes(asset.size) }))
      else toast.success(t('已导入 {{n}}', { n: asset.name }))
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const pick = async () => {
    if (window.a4s?.pickFile) {
      const path = await window.a4s.pickFile('media')
      if (path) await add(path)
      return
    }
    const { askForText } = await import('@/components/confirm')
    const path = await askForText({ title: t('导入背景素材'), input: { label: t('输入图片或视频的完整路径'), required: true } })
    if (path) await add(path)
  }

  const remove = async (asset: Asset) => {
    if (!(await confirmAction({ title: t('删除素材 {{n}}？', { n: asset.name }), danger: true }))) return
    await del(`/assets/${asset.id}`)
    await qc.invalidateQueries({ queryKey: ['assets'] })
    if (bg.asset === asset.id) await setBg({ asset: null, kind: null })
  }

  const pct = (v: number) => `${Math.round(v * 100)}%`

  return (
    <section className="max-w-2xl">
      <SectionTitle actions={
        <div className="flex gap-2">
          <Button size="sm" variant="outline" loading={busy} onClick={pick}><Image className="h-3.5 w-3.5" />{t('导入素材')}</Button>
          {bg.asset && <Button size="sm" variant="ghost" onClick={() => void setBg({ asset: null, kind: null })}>{t('不用背景')}</Button>}
        </div>
      }>{t('背景图片 / 视频')}</SectionTitle>
      <p className="mb-2 text-xs text-muted">
        {t('图片和视频都支持；视频会静音循环播放，窗口切到后台时自动暂停。素材复制到数据目录里保存，不会动你的原文件。')}
      </p>

      <div
        onDragOver={(e) => { e.preventDefault(); setDropping(true) }}
        onDragLeave={() => setDropping(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDropping(false)
          const file = e.dataTransfer.files?.[0]
          const path = file && window.a4s?.pathForFile(file)
          if (path) void add(path)
          else if (file) toast.error(t('浏览器模式下拿不到文件路径，请用「导入素材」。'))
        }}
        className={cn('flex flex-wrap gap-2 rounded-lg border border-dashed p-2 transition',
          dropping ? 'border-primary bg-primary/5' : 'border-border')}
      >
        {assets?.length
          ? assets.map((a) => <AssetChip key={a.id} asset={a} active={a.id === bg.asset}
              onPick={() => void setBg({ asset: a.id, kind: a.kind })} onRemove={() => void remove(a)} />)
          : <span className="px-2 py-6 text-center text-xs text-subtle">{t('还没有素材 — 点「导入素材」，或把文件拖到这里')}</span>}
      </div>
      {/* Media is not in a backup, so a restored install points at an asset that is gone. */}
      {bg.asset && assets && !current && (
        <p className="mt-2 text-xs text-warning">
          {t('选中的背景素材已经不在数据目录里（换了机器或恢复过备份？）。重新导入一次，或点「不用背景」。')}
        </p>
      )}

      {bg.asset && (
        <div className="mt-3 flex flex-col gap-2.5">
          <Field label={t('作用范围')}>
            <Segmented size="sm" value={bg.region} onChange={(v) => void setBg({ region: v })} options={[
              { value: 'window' as const, label: t('整个窗口') },
              { value: 'main' as const, label: t('仅主区域') },
              { value: 'sidebar' as const, label: t('仅侧边栏') },
            ]} />
          </Field>
          <Field label={t('填充方式')}>
            <Segmented size="sm" value={bg.fit} onChange={(v) => void setBg({ fit: v })} options={[
              { value: 'cover' as const, label: t('铺满') },
              { value: 'contain' as const, label: t('完整显示') },
              ...(bg.kind === 'image' ? [{ value: 'tile' as const, label: t('平铺') }] : []),
            ]} />
          </Field>
          <Slider label={t('不透明度')} value={bg.opacity} min={0.05} max={1} format={pct}
            onChange={(v) => void setBg({ opacity: v })} />
          <Slider label={t('模糊')} value={bg.blur} min={0} max={40} step={1} format={(v) => `${v}px`}
            onChange={(v) => void setBg({ blur: v })} />
          <Slider label={t('压暗')} value={bg.dim} min={0} max={0.9} format={pct}
            onChange={(v) => void setBg({ dim: v })} />
          {current?.kind === 'video' && (
            <p className="text-[11px] leading-relaxed text-warning">
              {t('背景视频会持续占用 GPU，笔记本上耗电明显。窗口失去焦点时会自动暂停，但看着它的时候一直在解码。')}
            </p>
          )}
        </div>
      )}
    </section>
  )
}

function AssetChip({ asset, active, onPick, onRemove }: { asset: Asset; active: boolean; onPick: () => void; onRemove: () => void }) {
  const { t } = useTranslation()
  const menu = useContextMenu([
    { label: t('用作背景'), onSelect: onPick },
    { label: t('删除素材'), icon: <Trash2 className="h-3.5 w-3.5" />, danger: true, separatorBefore: true, onSelect: onRemove },
  ])
  return (
    <>
      <button {...menu.trigger} onClick={onPick} title={`${asset.name} · ${formatBytes(asset.size)}`}
        className={cn('relative h-16 w-24 shrink-0 overflow-hidden rounded-md border bg-surface-2 transition',
          active ? 'border-primary ring-2 ring-primary/30' : 'border-border hover:border-primary/50')}>
        {asset.kind === 'image'
          ? <img src={assetUrl(asset.id)} alt={asset.name} className="h-full w-full object-cover" />
          : <video src={assetUrl(asset.id)} muted className="h-full w-full object-cover" />}
        <span className="absolute inset-x-0 bottom-0 flex items-center gap-1 bg-black/55 px-1 py-0.5 text-[10px] text-white">
          {asset.kind === 'video' && <Play className="h-2.5 w-2.5 shrink-0 fill-current" />}
          <Marquee>{asset.name}</Marquee>
        </span>
      </button>
      {menu.menu}
    </>
  )
}

/** Short synthesised cues at the moments you would otherwise have to watch for. */
function Sounds() {
  const { t } = useTranslation()
  const sound = useUi((s) => s.ui.sound)
  const setSound = useUi((s) => s.setSound)
  const cues: [Cue, string][] = [
    ['start', '开始任务'], ['done', '完成'], ['ask', '需要你回答'], ['error', '出错'],
  ]
  return (
    <section className="max-w-2xl">
      <SectionTitle>{t('音效')}</SectionTitle>
      <p className="mb-2 text-xs text-muted">{t('合成音，不含音频文件。默认关闭。')}</p>
      <div className="flex flex-col gap-2.5">
        <Switch checked={sound.enabled} onCheckedChange={(v) => { void setSound({ enabled: v }); if (v) playCue('done', { force: true }) }}
          label={<span className="flex items-center gap-1.5"><Volume2 className="h-3.5 w-3.5" />{t('启用音效提示')}</span>} />
        <Slider label={t('音量')} value={sound.volume} min={0} max={1} format={(v) => `${Math.round(v * 100)}%`}
          onChange={(v) => void setSound({ volume: v })} className="max-w-sm" />
        <div className="flex flex-wrap gap-2">
          {cues.map(([cue, label]) => (
            <Button key={cue} size="sm" variant="outline" onClick={() => playCue(cue, { force: true })}>
              <Play className="h-3 w-3" />{t(label)}
            </Button>
          ))}
        </div>
      </div>
    </section>
  )
}
