import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Globe, KeyRound, Lightbulb, Pencil, Plug, PlugZap, Plus, RefreshCw, Search, Terminal, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge, Button, Card, Dialog, Empty, Field, Input, PageHeader, SectionTitle, Segmented, Spinner, Switch, Textarea,
  Tip } from '@/components/ui'
import { confirmAction } from '@/components/confirm'
import { del, errorMessage, get, post, put } from '@/lib/api'
import { cn, toast } from '@/lib/utils'

interface ServerConfig {
  name: string
  command: string | null
  args: string[]
  env: Record<string, string>
  url: string | null
  headers: Record<string, string>
  enabled: boolean
  tools: string[] | null
  description: string
  origin: string
}
interface ServerRow extends ServerConfig {
  status: { name: string; ok: boolean; error: string | null; tools: string[] } | null
  missing_env: string[]
}
interface CatalogMCP { name: string; label: string; repo: string; license: string; domains: string[]; command: string | null; args: string[]; url: string | null; note?: string }
interface RegistryHit {
  name: string; description: string; version: string; repository: string | null; status: string | null
  config: ServerConfig | null
  env_vars: { name: string; description: string; required: boolean; secret: boolean }[]
}

const EMPTY: ServerConfig = { name: '', command: '', args: [], env: {}, url: null, headers: {}, enabled: true, tools: null, description: '', origin: 'manual' }
const REF = /\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g

function refsOf(cfg: ServerConfig): string[] {
  const out = new Set<string>()
  for (const v of [...Object.values(cfg.env), ...Object.values(cfg.headers), ...cfg.args, cfg.url ?? '']) {
    for (const m of v.matchAll(REF)) out.add(m[1])
  }
  return [...out].sort()
}
const toLines = (obj: Record<string, string>) => Object.entries(obj).map(([k, v]) => `${k}=${v}`).join('\n')
function fromLines(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const i = line.indexOf('=')
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim()
  }
  return out
}
/** A saved server whose launch line no longer matches the curated one it came from. */
function drifted(server: ServerRow, recommended?: CatalogMCP): boolean {
  if (!recommended || server.origin !== 'catalog') return false
  return commandLine(server) !== commandLine(recommended)
}

function commandLine(cfg: { command: string | null; args: string[]; url: string | null }): string {
  if (cfg.url) return cfg.url
  return [cfg.command, ...cfg.args.map((a) => (/\s/.test(a) ? `"${a}"` : a))].filter(Boolean).join(' ')
}

export default function McpPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['mcp'],
    queryFn: () => get<{ servers: ServerRow[]; catalog: CatalogMCP[] }>('/mcp'),
    // servers connect in the background after a save; poll until every enabled one reports
    refetchInterval: (q) => (q.state.data?.servers.some((s) => s.enabled && !s.status) ? 2500 : false),
  })
  const [editing, setEditing] = useState<{ config: ServerConfig; isNew: boolean; envHelp?: RegistryHit['env_vars'] } | null>(null)
  const [envFor, setEnvFor] = useState<ServerRow | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['mcp'] })
  const configured = new Set(data?.servers.map((s) => s.name))

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={t('MCP 服务器')}
        description={t('通过 Model Context Protocol 接入外部工具（论文检索、生物医学数据库、EEG 分析等）。连接成功后，其工具以 mcp__服务器__工具 的名字出现在 Harness 的“MCP 工具”组。')}
        actions={<Button variant="primary" onClick={() => setEditing({ config: { ...EMPTY }, isNew: true })}><Plus className="h-4 w-4" />{t('手动添加')}</Button>} />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="flex max-w-6xl flex-col gap-6">
          <div>
            <SectionTitle>{t('已配置')}</SectionTitle>
            {isLoading ? <Spinner /> : data?.servers.length ? (
              <div className="flex flex-col gap-2">
                {data.servers.map((s) => (
                  <ServerCard key={s.name} server={s} recommended={data.catalog.find((m) => m.name === s.name)}
                    onEdit={() => setEditing({ config: s, isNew: false })} onEnv={() => setEnvFor(s)} onChanged={refresh} />
                ))}
              </div>
            ) : (
              <Card><Empty icon={<Plug className="h-8 w-8" />} title={t('还没有 MCP 服务器')} hint={t('从下面的精选列表或官方注册表添加；stdio 服务器通常用 uvx / npx 启动，需要本机能访问网络下载。')} /></Card>
            )}
          </div>
          <div>
            <SectionTitle>{t('精选科研 MCP')}</SectionTitle>
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {data?.catalog.map((m) => (
                <Card key={m.name} className="flex flex-col gap-1.5 p-3">
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{t(m.label || m.name)}</span>
                    {configured.has(m.name) ? <Badge tone="success"><Check className="h-3 w-3" />{t('已添加')}</Badge> : (
                      <Button size="sm" variant="outline" onClick={() => setEditing({
                        isNew: true,
                        config: { ...EMPTY, name: m.name, command: m.command, args: m.args, url: m.url, description: m.label, origin: 'catalog' },
                      })}><Plus className="h-3.5 w-3.5" />{t('添加')}</Button>
                    )}
                  </div>
                  <div className="truncate font-mono text-[11px] text-muted">{commandLine(m)}</div>
                  {m.note && <div className="text-[11px] leading-relaxed text-subtle">{t(m.note)}</div>}
                  <div className="mt-auto flex flex-wrap items-center gap-1">
                    {m.license && <Badge tone={m.license === '未核实' ? 'warning' : 'neutral'}>{t(m.license)}</Badge>}
                    {m.domains.map((d) => <Badge key={d} tone="primary">{d}</Badge>)}
                    {m.repo && <span className="ml-auto truncate font-mono text-[10px] text-subtle">{m.repo}</span>}
                  </div>
                </Card>
              ))}
            </div>
          </div>
          <RegistrySearch configured={configured} onAdd={(hit) => setEditing({ isNew: true, config: { ...EMPTY, ...hit.config! }, envHelp: hit.env_vars })} />
        </div>
      </div>
      {editing && <ServerDialog initial={editing.config} isNew={editing.isNew} envHelp={editing.envHelp}
        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void refresh() }} />}
      {envFor && <EnvDialog names={refsOf(envFor)} missing={envFor.missing_env} server={envFor.name} onClose={() => setEnvFor(null)}
        onSaved={async () => {
          setEnvFor(null)
          if (envFor.enabled) await put(`/mcp/${encodeURIComponent(envFor.name)}`, { enabled: true })
          void refresh()
        }} />}
    </div>
  )
}

/** Turn a server's own error output into something actionable — most MCP failures are not config typos. */
function Diagnosis({ server, error, busy, onFix }: {
  server: ServerRow
  error: string | null
  busy: boolean
  onFix: (args: string[]) => void
}) {
  const { t } = useTranslation()
  if (!error) return null
  const pinned = server.args.some((a) => /^mcp<2$/.test(a))
  if (/fastmcp|FastMCP/.test(error) && server.command?.includes('uvx') && !pinned) {
    return (
      <Hint text={t('这个服务器是按旧版 MCP SDK（v1）写的，安装时却装到了 v2。给启动参数加上 --with "mcp<2" 就能用它要求的版本运行。')}>
        <Button size="sm" variant="primary" loading={busy} onClick={() => onFix(['--with', 'mcp<2', ...server.args])}>
          {t('加上 --with "mcp<2" 并重连')}
        </Button>
      </Hint>
    )
  }
  // uvx runs the executable named after the package; plenty of packages install a differently named one.
  const wrongName = error.match(/executable `([^`]+)` was not found/) ?? error.match(/Failed to spawn: `([^`]+)`/)
  if (wrongName && server.command?.includes('uvx')) {
    const suggested = error.match(/an executable with the name `([^`]+)`/i)?.[1]
    const rest = server.args.filter((a) => a !== wrongName[1])
    return (
      <Hint text={suggested
        ? t('包名（{{pkg}}）和它安装的命令（{{exe}}）不一样，uvx 需要 --from 才能分辨。', { pkg: wrongName[1], exe: suggested })
        : t('uvx 在 {{pkg}} 里找不到同名的可执行文件：这个包安装的命令可能叫别的名字。', { pkg: wrongName[1] })}>
        {suggested && (
          <Button size="sm" variant="primary" loading={busy} onClick={() => onFix(['--from', wrongName[1], suggested, ...rest])}>
            {t('改成 --from {{pkg}} {{exe}} 并重连', { pkg: wrongName[1], exe: suggested })}
          </Button>
        )}
      </Hint>
    )
  }
  if (/ENOENT|No such file|command not found|找不到/.test(error)) {
    return <Hint text={t('找不到启动命令。uvx 需要 uv（安装包已自带），npx 需要自己装 Node.js；也可以把命令改成完整路径。')} />
  }
  if (/tls|handshake|Failed to fetch|getaddrinfo|Connection reset|proxy/i.test(error)) {
    return <Hint text={t('下载依赖时网络失败。应用会自动使用系统代理；如果代理需要开启“允许局域网连接”或走 PAC，请先确认浏览器能访问 pypi.org / npmjs.com，然后点“重新连接”。')} />
  }
  if (/超时|timeout/i.test(error)) {
    return <Hint text={t('首次启动要下载几十个依赖包，网络慢时会超时。再点一次“重新连接”通常就能成功（依赖已缓存）。')} />
  }
  if (server.missing_env.length > 0) {
    return <Hint text={t('这个服务器需要先填环境变量（上面的钥匙按钮）。')} />
  }
  return null
}

function Hint({ text, children }: { text: string; children?: ReactNode }) {
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-2 rounded bg-info/8 px-2 py-1.5 text-[11px] text-muted">
      <Lightbulb className="h-3.5 w-3.5 shrink-0 text-info" />
      <span className="min-w-0 flex-1">{text}</span>
      {children}
    </div>
  )
}

function ServerCard({ server, recommended, onEdit, onEnv, onChanged }: {
  server: ServerRow
  recommended?: CatalogMCP
  onEdit: () => void
  onEnv: () => void
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState<string | null>(null)
  const [test, setTest] = useState<ServerRow['status'] | null>(null)
  const [showTools, setShowTools] = useState(false)
  const s = server.status
  const state = !server.enabled ? 'off' : !s ? 'pending' : s.ok ? 'ok' : 'error'
  const dot = { off: 'bg-border', pending: 'bg-info animate-pulse', ok: 'bg-success', error: 'bg-danger' }[state]
  const label = { off: t('已停用'), pending: t('连接中…'), ok: t('已连接'), error: t('连接失败') }[state]
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

  return (
    <Card className="p-3">
      <div className="flex items-center gap-3">
        <span className={cn('h-2.5 w-2.5 shrink-0 rounded-full', dot)} />
        {server.url ? <Globe className="h-4 w-4 shrink-0 text-muted" /> : <Terminal className="h-4 w-4 shrink-0 text-muted" />}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{server.name}</span>
            <span className="text-xs text-muted">{label}</span>
            {s?.ok && <button className="text-xs text-primary hover:underline" onClick={() => setShowTools(!showTools)}>{t('{{n}} 个工具', { n: s.tools.length })}</button>}
            {server.origin && <Badge>{server.origin.startsWith('registry:') ? t('注册表') : t(server.origin === 'catalog' ? '精选' : '手动')}</Badge>}
          </div>
          <div className="truncate font-mono text-[11px] text-muted">{commandLine(server)}</div>
          {server.description && <div className="truncate text-xs text-subtle">{server.description}</div>}
        </div>
        {server.missing_env.length ? (
          <Tip content={t('这些变量还没有值：{{v}}', { v: server.missing_env.join(', ') })}>
            <Button size="sm" variant="outline" className="border-warning/60 text-warning" onClick={onEnv}><KeyRound className="h-3.5 w-3.5" />{t('缺少 {{n}} 个变量', { n: server.missing_env.length })}</Button>
          </Tip>
        ) : refsOf(server).length > 0 && (
          <Tip content={t('修改变量的值')}><Button size="icon" variant="ghost" onClick={onEnv}><KeyRound className="h-3.5 w-3.5" /></Button></Tip>
        )}
        <Tip content={t('测试连接（独立启动一次并列出工具）')}>
          <Button size="sm" variant="ghost" loading={busy === 'test'} onClick={() => run('test', async () => {
            const res = await post<NonNullable<ServerRow['status']>>(`/mcp/${encodeURIComponent(server.name)}/test`)
            setTest(res)
            if (res.ok) toast.success(t('{{n}} 连接成功：{{k}} 个工具', { n: server.name, k: res.tools.length }))
          })}><PlugZap className="h-3.5 w-3.5" />{t('测试')}</Button>
        </Tip>
        <Tip content={t('重新连接')}>
          <Button size="icon" variant="ghost" disabled={!server.enabled} loading={busy === 'restart'} onClick={() => run('restart', async () => {
            await put(`/mcp/${encodeURIComponent(server.name)}`, { enabled: true })
            onChanged()
          })}>{busy !== 'restart' && <RefreshCw className="h-3.5 w-3.5" />}</Button>
        </Tip>
        <Switch checked={server.enabled} onCheckedChange={(v) => run('toggle', async () => {
          await put(`/mcp/${encodeURIComponent(server.name)}`, { enabled: v })
          onChanged()
        })} />
        <Tip content={t('编辑启动命令、参数与环境变量')}>
          <Button size="icon" variant="ghost" onClick={onEdit}><Pencil className="h-3.5 w-3.5" /></Button>
        </Tip>
        <Button size="icon" variant="ghost" className="text-danger" onClick={() => run('delete', async () => {
          if (!(await confirmAction({ title: t('删除 MCP 服务器 {{n}}？', { n: server.name }), danger: true }))) return
          await del(`/mcp/${encodeURIComponent(server.name)}`)
          onChanged()
        })}><Trash2 className="h-3.5 w-3.5" /></Button>
      </div>
      {state === 'error' && s?.error && <div className="mt-2 rounded bg-danger/10 px-2 py-1 font-mono text-[11px] whitespace-pre-wrap text-danger">{s.error}</div>}
      {test && !test.ok && <div className="mt-2 rounded bg-danger/10 px-2 py-1 font-mono text-[11px] whitespace-pre-wrap text-danger">{t('测试失败')}：{test.error}</div>}
      {drifted(server, recommended) && (
        <Hint text={t('这个服务器的推荐启动方式后来改过（通常是上游改了包名或命令）。当前：{{now}}；推荐：{{next}}。', {
          now: commandLine(server), next: commandLine(recommended!) })}>
          <Button size="sm" variant="primary" loading={busy === 'sync'} onClick={() => run('sync', async () => {
            await put(`/mcp/${encodeURIComponent(server.name)}`, { command: recommended!.command, args: recommended!.args, enabled: true })
            onChanged()
            toast.success(t('已更新为推荐配置'))
          })}>{t('更新为推荐配置')}</Button>
        </Hint>
      )}
      <Diagnosis server={server} error={s?.error ?? test?.error ?? null} busy={busy === 'fix'}
        onFix={(args) => run('fix', async () => {
          await put(`/mcp/${encodeURIComponent(server.name)}`, { args, enabled: true })
          onChanged()
          toast.success(t('已修改启动参数并重新连接'))
        })} />
      {showTools && s?.ok && (
        <div className="mt-2 flex flex-wrap gap-1">{s.tools.map((tl) => <Badge key={tl} className="font-mono">{tl}</Badge>)}</div>
      )}
    </Card>
  )
}

function RegistrySearch({ configured, onAdd }: { configured: Set<string>; onAdd: (hit: RegistryHit) => void }) {
  const { t } = useTranslation()
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const { data, isFetching, error } = useQuery({
    queryKey: ['mcp-registry', query],
    queryFn: () => get<RegistryHit[]>('/mcp/registry', { q: query }),
    enabled: !!query,
    staleTime: 5 * 60_000,
  })
  return (
    <div>
      <SectionTitle>{t('官方 MCP 注册表')}</SectionTitle>
      <form className="mb-3 flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()) }}>
        <div className="relative w-96 max-w-full">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <Input className="pl-8" value={q} placeholder={t('按名称检索，如 pubmed、chembl、zotero')} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Button type="submit" variant="outline" disabled={!q.trim()} loading={isFetching}>{t('检索')}</Button>
        <span className="text-[11px] text-subtle">registry.modelcontextprotocol.io · {t('注册表只按名称匹配')}</span>
      </form>
      {error && <div className="text-xs text-danger">{errorMessage(error)}</div>}
      <div className="flex flex-col gap-2">
        {data?.map((hit) => {
          const name = hit.config?.name
          return (
            <Card key={`${hit.name}@${hit.version}`} className="flex items-start gap-3 p-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-[13px] font-medium">{hit.name}</span>
                  {hit.version && <Badge>v{hit.version}</Badge>}
                  {hit.status && hit.status !== 'active' && <Badge tone="warning">{hit.status}</Badge>}
                </div>
                <div className="mt-0.5 line-clamp-2 text-xs text-muted">{hit.description}</div>
                {hit.config && <div className="mt-1 truncate font-mono text-[11px] text-subtle">{commandLine(hit.config)}</div>}
                {!!hit.env_vars.length && (
                  <div className="mt-1 flex flex-wrap gap-1">{hit.env_vars.map((v) => (
                    <Tip key={v.name} content={v.description || v.name}><span><Badge tone={v.required ? 'warning' : 'neutral'}>{v.secret && <KeyRound className="h-3 w-3" />}{v.name}</Badge></span></Tip>
                  ))}</div>
                )}
              </div>
              {name && configured.has(name) ? <Badge tone="success"><Check className="h-3 w-3" />{t('已添加')}</Badge> : (
                <Tip content={hit.config ? t('添加并启动') : t('没有可用的 stdio 包或远程地址，无法自动生成启动命令')}>
                  <span><Button size="sm" variant="outline" disabled={!hit.config} onClick={() => onAdd(hit)}><Plus className="h-3.5 w-3.5" />{t('添加')}</Button></span>
                </Tip>
              )}
            </Card>
          )
        })}
        {data && !data.length && <div className="text-xs text-subtle">{t('没有结果')}</div>}
      </div>
    </div>
  )
}

function ServerDialog({ initial, isNew, envHelp, onClose, onSaved }: {
  initial: ServerConfig; isNew: boolean; envHelp?: RegistryHit['env_vars']; onClose: () => void; onSaved: () => void
}) {
  const { t } = useTranslation()
  const [cfg, setCfg] = useState<ServerConfig>(initial)
  const [transport, setTransport] = useState<'stdio' | 'http'>(initial.url ? 'http' : 'stdio')
  const [argsText, setArgsText] = useState(initial.args.join('\n'))
  const [envText, setEnvText] = useState(toLines(initial.env))
  const [headersText, setHeadersText] = useState(toLines(initial.headers))
  const [toolsText, setToolsText] = useState((initial.tools ?? []).join(', '))
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const set = (patch: Partial<ServerConfig>) => setCfg({ ...cfg, ...patch })

  const build = (): ServerConfig => ({
    ...cfg,
    name: cfg.name.trim(),
    command: transport === 'stdio' ? (cfg.command ?? '').trim() : null,
    args: transport === 'stdio' ? argsText.split('\n').map((a) => a.trim()).filter(Boolean) : [],
    env: transport === 'stdio' ? fromLines(envText) : {},
    url: transport === 'http' ? (cfg.url ?? '').trim() : null,
    headers: transport === 'http' ? fromLines(headersText) : {},
    tools: toolsText.trim() ? toolsText.split(',').map((x) => x.trim()).filter(Boolean) : null,
  })
  const refs = refsOf(build())
  const valid = /^[\w.-]+$/.test(cfg.name.trim()) && (transport === 'stdio' ? !!cfg.command?.trim() : /^https?:\/\//.test(cfg.url ?? ''))

  const save = async () => {
    setBusy(true)
    try {
      for (const [k, v] of Object.entries(secrets)) if (v) await put(`/mcp-env/${k}`, { value: v })
      const body = build()
      if (isNew) await post('/mcp', body)
      else await put(`/mcp/${encodeURIComponent(initial.name)}`, body)
      toast.success(body.enabled ? t('已保存，正在后台连接 {{n}}', { n: body.name }) : t('已保存'))
      onSaved()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-2xl" title={isNew ? t('添加 MCP 服务器') : t('编辑 {{n}}', { n: initial.name })}
      description={t('外部 MCP 服务器是第三方代码：只添加你信任的来源。环境变量值可写成 ${VAR}，实际值保存在系统钥匙串。')}
      footer={<><Button variant="ghost" onClick={onClose}>{t('取消')}</Button>
        <Button variant="primary" disabled={!valid} loading={busy} onClick={save}>{isNew ? t('添加并连接') : t('保存')}</Button></>}>
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('名称（工具前缀）')}><Input value={cfg.name} disabled={!isNew} placeholder="pubmed" onChange={(e) => set({ name: e.target.value })} /></Field>
          <Field label={t('连接方式')}>
            <Segmented value={transport} onChange={setTransport} options={[{ value: 'stdio', label: t('本地进程 stdio') }, { value: 'http', label: t('远程 HTTP') }]} />
          </Field>
        </div>
        {transport === 'stdio' ? (
          <>
            <Field label={t('命令')} hint={t('常用：uvx（Python 包）、npx（Node 包）；会自动在登录环境的 PATH 中查找')}>
              <Input className="font-mono" value={cfg.command ?? ''} placeholder="uvx" onChange={(e) => set({ command: e.target.value })} />
            </Field>
            <Field label={t('参数（每行一个）')}><Textarea className="font-mono text-xs" rows={3} value={argsText} onChange={(e) => setArgsText(e.target.value)} /></Field>
            <Field label={t('环境变量（每行 KEY=value）')}><Textarea className="font-mono text-xs" rows={3} value={envText} placeholder="NCBI_API_KEY=${NCBI_API_KEY}" onChange={(e) => setEnvText(e.target.value)} /></Field>
          </>
        ) : (
          <>
            <Field label="URL"><Input className="font-mono" value={cfg.url ?? ''} placeholder="https://example.org/mcp" onChange={(e) => set({ url: e.target.value })} /></Field>
            <Field label={t('请求头（每行 Name=value）')}><Textarea className="font-mono text-xs" rows={2} value={headersText} placeholder="Authorization=Bearer ${MY_TOKEN}" onChange={(e) => setHeadersText(e.target.value)} /></Field>
          </>
        )}
        {refs.length > 0 && (
          <div className="rounded-md border border-border p-3">
            <div className="mb-2 text-xs font-medium text-muted">{t('变量的值（保存到系统钥匙串；已设置过的可留空）')}</div>
            <div className="flex flex-col gap-2">
              {refs.map((r) => {
                const help = envHelp?.find((v) => v.name === r)
                return (
                  <div key={r} className="flex items-center gap-2">
                    <span className="w-44 shrink-0 truncate font-mono text-xs">{r}{help?.required && <span className="text-danger"> *</span>}</span>
                    <Input type="password" autoComplete="off" className="h-7 text-xs" placeholder={help?.description || t('值')}
                      value={secrets[r] ?? ''} onChange={(e) => setSecrets({ ...secrets, [r]: e.target.value })} />
                  </div>
                )
              })}
            </div>
          </div>
        )}
        <Field label={t('说明')}><Input value={cfg.description} onChange={(e) => set({ description: e.target.value })} /></Field>
        <Field label={t('只启用这些工具（逗号分隔，空 = 全部）')}><Input className="font-mono text-xs" value={toolsText} onChange={(e) => setToolsText(e.target.value)} /></Field>
        <Switch checked={cfg.enabled} onCheckedChange={(v) => set({ enabled: v })} label={t('启用')} />
      </div>
    </Dialog>
  )
}

function EnvDialog({ names, missing, server, onClose, onSaved }: { names: string[]; missing: string[]; server: string; onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={t('{{n}} 的环境变量', { n: server })}
      description={t('值保存在系统钥匙串（macOS 钥匙串 / Windows 凭据管理器），不会写入配置文件。')}
      footer={<><Button variant="ghost" onClick={onClose}>{t('取消')}</Button>
        <Button variant="primary" loading={busy} onClick={async () => {
          setBusy(true)
          try {
            for (const [k, v] of Object.entries(values)) if (v) await put(`/mcp-env/${k}`, { value: v })
            onSaved()
          } catch (err) {
            toast.error(errorMessage(err))
          } finally {
            setBusy(false)
          }
        }}>{t('保存并重新连接')}</Button></>}>
      <div className="flex flex-col gap-2">
        {names.map((n) => (
          <div key={n} className="flex items-center gap-2">
            <span className="w-44 shrink-0 truncate font-mono text-xs">{n}</span>
            <Input type="password" autoComplete="off" className="h-7 text-xs" value={values[n] ?? ''}
              placeholder={missing.includes(n) ? t('未设置') : t('已设置（留空不修改）')} onChange={(e) => setValues({ ...values, [n]: e.target.value })} />
          </div>
        ))}
      </div>
    </Dialog>
  )
}
