// The boundaries of this software, in one place: shown as a Settings tab, and once as a blocking
// first-run consent so the app can be handed to someone else.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ScrollText } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Card, SectionTitle } from '@/components/ui'
import { errorMessage, get, put } from '@/lib/api'
import { toast } from '@/lib/utils'

/** Sections as [heading, paragraphs]; the consent dialog and the Settings tab render the same list. */
function useSections(): [string, string[]][] {
  const { t } = useTranslation()
  return [
    [t('这个软件做什么'), [
      t('Agent4Sci 是一个自动化科研工作流工具：它按你的指令调用大模型，检索文献、读写你工作区里的文件、运行命令与 Python、调用 MCP 工具，并把过程记录下来。'),
      t('它是一个助手，不是一个研究者。所有产出都是草稿，需要你自己核对。'),
    ]],
    [t('它不替代什么'), [
      t('不提供医疗、法律、投资或任何专业建议；它给出的结论不构成决策依据。'),
      t('不保证结论正确。模型会编造看似可信的引文、数字和因果关系。工具里的「成果自检」能查出证据链断裂和凭空数字，但查不出推理本身的错误——发表前的核对责任在你。'),
      t('不保证可复现。同一个提问在不同时间、不同模型上会得到不同结果。'),
    ]],
    [t('你的数据在哪里'), [
      t('对话、运行记录、记忆、模型档案都存在本机的数据目录里（设置→关于里可以直接打开）。项目文件就是你自己磁盘上的文件夹。'),
      t('API Key 存在系统钥匙串（macOS 钥匙串 / Windows 凭据管理器），不写进数据库、不进日志、不进备份。备份恢复后需要重新填。'),
      t('这个软件自己不往任何服务器上传东西。但它按你的配置把对话内容、文件片段发给你选择的模型提供商（Anthropic / DeepSeek / OpenAI / 本地 Ollama 等）和你开启的检索服务（arXiv、Crossref、PubMed、Semantic Scholar 等）。这些服务怎么处理你的数据，由它们的条款决定；涉密或涉及个人隐私的数据请先想清楚再放进来。'),
    ]],
    [t('第三方代码的风险'), [
      t('技能（Agent Skills）和 MCP 服务器是别人写的代码，安装后以你的身份运行。安装前会扫描可疑模式并要你确认，但那是提示，不是沙箱——它拦不住一个刻意写坏的技能。'),
      t('只安装你看得懂、或来源可信的技能。'),
    ]],
    [t('命令执行与无人值守'), [
      t('Agent 能在你的工作区里跑命令和 Python。文件工具限制在工作区内，但命令行没有这个限制——它能做的事等于你在终端里能做的事。'),
      t('权限模式决定它问不问你：「总是询问」每一步都确认，「按需提问」只在危险动作上停，「全部允许」不再询问。Loop 的无人值守模式（auto_mode）会在你不看着的时候连续执行——用它之前先确认这个 Loop 你已经跑通过。'),
    ]],
    [t('桌面控制（后续版本）'), [
      t('后续版本会提供「桌面控制」：截屏、移动鼠标、敲键盘、打开应用。先把边界说清楚：'),
      t('这是辅助功能级别的自动化，不是沙箱。它的权限等于你本人的权限——你的邮箱、聊天记录、银行页面，只要在屏幕上，它就能看见和点击。'),
      t('需要你在系统设置里手动授予「屏幕录制」和「辅助功能」权限，授权对象是 Agent4Sci 这个应用。不授权就用不了，这是设计如此。'),
      t('模型点错位置是常态而不是例外。对不可逆的操作（发送、删除、购买、提交）会强制询问，但不要指望它永远点对。'),
      t('用它去刷视频、挂游戏、自动化社交平台，违反大多数平台的服务条款，账号可能被封——这个风险由你承担。这个软件不会为绕过平台的反自动化检测做优化，也不会内置任何针对具体平台的脚本。'),
    ]],
    [t('费用'), [
      t('模型 API 和检索服务的费用由你自己承担。界面会估算每次运行的花费，但它基于公开价目表，可能和账单有差异——真实花费请看提供商的账单页面。'),
      t('Harness 里的轮数和 token 上限可以设为无上限。设成无上限意味着一次运行的花费也没有上限。'),
    ]],
    [t('免责声明'), [
      t('本软件按 MIT 许可证提供，不附带任何形式的担保。使用它造成的任何数据损失、费用、学术或法律后果，作者与贡献者不承担责任。'),
      t('一句话版本：它是你的工具，不是你的担保人。'),
    ]],
  ]
}

function Body({ compact }: { compact?: boolean }) {
  const all = useSections()
  return (
    <div className={compact ? 'flex flex-col gap-4' : 'flex max-w-3xl flex-col gap-5'}>
      {all.map(([heading, paragraphs]) => (
        <section key={heading}>
          <h3 className="mb-1.5 text-sm font-semibold">{heading}</h3>
          <div className="flex flex-col gap-1.5">
            {paragraphs.map((p) => (
              <p key={p} className="text-xs leading-relaxed text-muted">{p}</p>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

/** Settings → 协议与声明 */
export function TermsTab() {
  const { t } = useTranslation()
  const { data } = useQuery({ queryKey: ['settings'], queryFn: () => get<{ terms_accepted_at: number | null }>('/settings') })
  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <Card className="flex items-start gap-3 p-4">
        <ScrollText className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
        <div className="text-xs leading-relaxed text-muted">
          {t('这一页写明这个软件的能力边界、数据去向和你需要承担的责任。它也是第一次启动时要你同意的那份文本。')}
          {data?.terms_accepted_at
            ? ` ${t('你已于 {{d}} 同意。', { d: new Date(data.terms_accepted_at * 1000).toLocaleString() })}`
            : ''}
        </div>
      </Card>
      <Body />
      <section>
        <SectionTitle>{t('开源许可')}</SectionTitle>
        <p className="text-xs leading-relaxed text-muted">
          {t('Agent4Sci 本体以 MIT 许可证发布（Copyright © 2026 Agent4Sci contributors，完整条款见安装目录里的 LICENSE 文件）。')}
        </p>
        <p className="mt-1.5 text-xs leading-relaxed text-muted">
          {t('它建立在这些开源项目之上，各自遵循自己的许可证：后端 FastAPI、uvicorn、pydantic、httpx、keyring、PyYAML、anthropic 与 openai 的 Python SDK、MCP SDK、jupyter-client、GitPython；前端 Electron、React、Vite、TanStack Query、zustand、Radix UI、Tailwind CSS、lucide、recharts、react-markdown、i18next、CodeMirror。')}
        </p>
      </section>
    </div>
  )
}

/** First run: a blocking sheet. Deliberately not the shared Dialog — there is no X and no Esc. */
export function Terms() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [agreed, setAgreed] = useState(false)
  const [busy, setBusy] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['terms'],
    queryFn: () => get<{ terms_accepted_at: number | null }>('/settings'),
    retry: 3,
  })
  if (isLoading || !data || data.terms_accepted_at) return null

  const accept = async () => {
    setBusy(true)
    try {
      await put('/settings', { terms_accepted_at: Date.now() / 1000 })
      await qc.invalidateQueries({ queryKey: ['terms'] })
      await qc.invalidateQueries({ queryKey: ['settings'] })
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm">
      <div className="flex max-h-[88vh] w-full max-w-2xl flex-col rounded-xl border border-border bg-elevated shadow-2xl">
        <div className="border-b border-border px-5 py-4">
          <h2 className="text-base font-semibold">{t('使用前请读一遍')}</h2>
          <p className="mt-1 text-xs text-muted">{t('Agent4Sci 会以你的身份读写文件、运行命令、调用付费接口。下面是它的边界。')}</p>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4"><Body compact /></div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-3">
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)}
              className="h-3.5 w-3.5 accent-[var(--a4s-primary)]" />
            {t('我已读完，并接受上面的边界与风险')}
          </label>
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1 text-[11px] text-subtle">
              <AlertTriangle className="h-3 w-3" />{t('设置→协议与声明 里随时可以再看')}
            </span>
            <Button variant="primary" disabled={!agreed} loading={busy} onClick={accept}>{t('同意并开始使用')}</Button>
          </div>
        </div>
      </div>
    </div>
  )
}
