# Agent4Sci

面向自动化科研的 Agent 框架与桌面应用（macOS / Windows）。支持多模型（Anthropic、OpenAI 以及任意 OpenAI 兼容端点）、可从外部查找并安装的 **Agent Skills** 与 **MCP** 工具、分层**记忆**、可视化 **Harness** 与 **Loop Engineering**（节点图设计循环并实时监控运行）。

主要面向四类研究：神经科学 / 脑科学、AI / 机器学习计算实验、生命科学 / 化学 / 材料，以及通用的文献综述与数据分析。

---

## 下载安装

到 [Releases](../../releases) 下载对应系统的安装包。没有现成安装包，或者你的系统不在下面的范围里，可以[自己构建](#安装与构建)——自己构建出来的应用不会被 Gatekeeper 拦。

### macOS（仅 Apple Silicon：M 系列芯片）

下载 `.dmg`，拖进「应用程序」。安装包**没有经过 Apple 公证**，所以第一次打开会被系统拦下，提示大意是「Apple 无法验证此 App 是否包含恶意软件」。放行一次就好，之后再也不会出现：

1. 先双击打开一次（会被拒绝 —— 这一步不能跳过，否则下一步看不到入口）
2. 打开**系统设置 → 隐私与安全性**，滚到下方会出现一行关于 Agent4Sci 被阻止的提示，点**「仍要打开」**
3. 再确认一次

> macOS 15 之前可以直接右键点应用 →「打开」绕过；macOS 15 起 Apple 取消了这条捷径，要走上面的系统设置。
> 熟悉终端的话，一条命令等效：`xattr -dr com.apple.quarantine /Applications/Agent4Sci.app`

Intel Mac 用不了预编译包（里面的后端是 arm64 的），只能自己构建。

### Windows（x64）

下载 `.exe` 安装。安装包未签名，SmartScreen 会拦：点「更多信息」→「仍要运行」。

### 装完还要做一步

应用本身**不含任何模型**。首次启动会先让你读一遍使用协议，然后需要到**模型**页填一个 API Key（DeepSeek、Anthropic、OpenAI，或任意 OpenAI 兼容端点），或者在本机装 [Ollama](https://ollama.com) 用本地模型。调用模型的费用由你自己承担。

只想先看看界面的话，到**设置 → 通用**里打开「启用演示模型」，不需要任何 Key 就能走完整个流程。

---

## 功能概览

| 模块 | 说明 |
|---|---|
| **模型** | 可以添加任意多个模型，每个单独设置 base URL、API Key、模型名、推理强度、上下文长度、价格和能力开关。Key 存在系统钥匙串（macOS 钥匙串 / Windows 凭据管理器）。内置预设：Anthropic、OpenAI、DeepSeek、通义千问、Kimi、智谱、SiliconFlow、OpenRouter、Gemini、Ollama、vLLM。可以把不同角色（例如评审）映射到不同模型。 |
| **对话** | 流式输出、推理摘要、工具调用卡片、审批按钮、运行中插话、上下文占用仪表、“记住这条”、从对话中整理记忆。 |
| **记忆** | 四种模式：🌐 全局（全局＋项目＋本对话）、📁 项目（项目＋本对话）、💬 仅本对话、🚫 无记忆。**临时对话** = 无记忆、只在内存中，关闭即丢弃（可“转为正式对话”）。记忆是 Markdown 笔记，系统提示只注入索引（`MEMORY.md`），全文按需读取；疑似提示注入的内容会被标记。 |
| **项目** | 一个项目就是一个工作区文件夹（文献、数据、代码、结果、图、论文、项目记忆、项目技能），每一步自动 git 提交，可回滚。自带 uv 创建 Python 环境，无需预装 Python。 |
| **Loop Engineering** | 节点图编辑器：Agent、想法锦标赛、命令/验证门、工具、人工检查点、条件、**循环容器**（停止条件、最大轮数、预算、指标、改进才保留否则 git 回滚、停滞检测）、并行展开、子流程、规则检查、数字渲染、记忆、技能沉淀。内置 10 个模板：完整科研流水线、文献综述、数据分析、ML 实验、Autoresearch、Ralph、生成→批评→改进、评审→修改、Best-of-N、想法锦标赛。 |
| **运行监控** | 同一张图实时着色、循环轮次、节点输出、时间线（缩进显示子代理）、指标曲线（保留 / 回滚标记）、人工检查点弹窗、暂停 / 继续 / 取消；应用重启后可从断点继续。 |
| **Harness** | 可视化 Agent 的构成：模型、提示块（每块的 token 数）、工具组开关、可见技能、MCP、记忆作用域、审批策略；实时检查器显示逐轮 token（含缓存命中）、成本、工具调用、上下文增长。内置 6 个档案：研究助手、文献专员、数据分析师、实验员、写作者、评审。 |
| **技能** | 遵循 [Agent Skills](https://agentskills.io/specification) 开放标准（`SKILL.md`）。从 GitHub（`owner/repo[/path][@ref]`）、git 地址或本地文件夹安装；**安装期不执行任何代码**，固定到 commit 并记录在 `skills.lock`；检查许可证（默认只放行开源许可证）；安全扫描（注入话术、隐藏字符、`curl \| sh`、读取凭据、持久化等），高风险默认拒绝。内置精选目录可联网建立索引后离线检索，并提供按领域的技能包。Agent 可在运行后**沉淀技能**，经你批准才会启用。 |
| **MCP** | 精选科研 MCP（arXiv、多源论文检索、PubMed、BioMCP、MNE-MCP、Virtual Fly Brain）、官方注册表检索、手动添加（stdio / HTTP）；`${VAR}` 形式的密钥存钥匙串；一键测试连接；启动失败时显示服务器输出。 |
| **科研工具** | 文献检索（arXiv、Semantic Scholar、OpenAlex、Crossref、PubMed，去重、限速、缓存），**只收录真实检索到的参考文献**（BibTeX 走 DOI 内容协商）与引用检查；**发现账本**（每条结论挂证据并自动核验）；**数字溯源**（论文中的数字写成 `{{results/main.json#/acc/mean:.3f}}` 占位符，渲染时从结果文件取值）；神经数据检索（DANDI、OpenNeuro、NeuroStore、Allen）。 |
| **界面** | 中文 / English；6 套内置主题 + 跟随系统；**主题编辑器**可调颜色、字体、字号、圆角、密度，带 WCAG 对比度检查，可导入导出 JSON；侧边栏可放左右；可导入**背景图片或循环视频**（范围、不透明度、模糊、压暗可调，窗口失焦自动暂停）；任务开始 / 完成 / 需要回答 / 出错可以响一声（合成音，默认关闭）；列表项右键直接复制、重命名、导出或删除。 |
| **命令行** | `a4s`：与桌面应用共用数据、钥匙串、项目与 Loop。 |

---

## 安装与构建

安装包需要在对应系统上构建（macOS 构建 dmg，Windows 构建 exe）。首次构建需要联网下载依赖。

### macOS（Apple Silicon）

前置：[uv](https://docs.astral.sh/uv/getting-started/installation/)、Node.js 20+。

```bash
packaging/build-mac.sh
```

产物：`desktop/release/Agent4Sci-1.0.0-mac-arm64.dmg`（以及 `.zip`）。脚本会依次：安装依赖 → 运行测试 → 用 PyInstaller 打包 Python 后端 → 下载并校验 uv 二进制 → 冒烟测试后端 → 构建 Electron 应用 → ad-hoc 签名 → 生成 dmg/zip。加 `--skip-tests` 可跳过测试。

你在本机构建出来的应用**不会**被 Gatekeeper 拦（quarantine 标记是下载时才加的）。把 dmg 发给别人时才需要放行，步骤见上面的[下载安装](#下载安装)。

### Windows（x64）

在仓库根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-win.ps1
```

缺少 uv 或 Node.js 时，脚本会询问是否自动安装（uv 用官方安装脚本，Node.js 用 winget）；加 `-Yes` 自动同意，加 `-SkipTests` 跳过测试。产物：`desktop\release\Agent4Sci-Setup-1.0.0.exe`。未签名的安装包首次运行时 SmartScreen 可能拦截：点“更多信息”→“仍要运行”。

git 不是构建必需，但应用里的工作区版本记录与回滚需要它（[git-scm.com](https://git-scm.com/)）。

### 要把安装包发给别人（签名与公证）

不签名也能用，但别人第一次打开会被系统拦下（macOS 提示“无法验证开发者”，Windows 是 SmartScreen），需要他们手动放行。正式分发建议签名，两个脚本都会自动识别环境变量里的证书，不用改配置：

```bash
# macOS：Developer ID Application 证书（在登录钥匙串里）+ 公证账号
CSC_NAME="Developer ID Application: 你的名字 (TEAMID)" \
APPLE_ID=you@example.com APPLE_APP_SPECIFIC_PASSWORD=xxxx-xxxx-xxxx-xxxx APPLE_TEAM_ID=TEAMID \
  packaging/build-mac.sh
```

```powershell
# Windows：代码签名证书（.pfx）
$env:CSC_LINK = "C:\path\to\cert.pfx"; $env:CSC_KEY_PASSWORD = "..."
powershell -ExecutionPolicy Bypass -File packaging\build-win.ps1
```

证书需要自己申请：Apple Developer Program（99 美元/年）、Windows 代码签名证书（各 CA 价格不同）。没有证书时，让对方按[下载安装](#下载安装)里的步骤放行一次即可（macOS 走系统设置，Windows 点“更多信息 → 仍要运行”）。

---

## 快速开始

1. **添加模型**：「模型」→「添加模型」，选服务商预设，填模型名（例如 `claude-opus-5`、`gpt-5`、`deepseek-chat`）和 API Key，点「测试连接」（会同时检测工具调用）。不想马上填 Key，可以在对话页点「启用演示模型」先体验完整流程（离线脚本模型，不产生费用）。除了内置价格表里的 Claude 模型，云端模型请在「编辑 → 高级设置」里填写单价（输入 / 输出 / 缓存命中，美元每百万 token）：不填价格时，费用只显示为“—”，Loop 的预算上限也无法生效；本地模型（Ollama 等）按零成本计。
2. **新建项目**：「项目」→「新建项目」，选研究领域（决定默认工具、技能与提示），可勾选创建 Python 环境和安装该领域的技能包。
3. **对话**：在项目里开一个对话，选记忆模式（或开临时对话）。需要审批的工具调用会在对话里出现“允许 / 拒绝”。
4. **跑 Loop**：「Loop 设计器」选一个模板（例如「文献综述」），点「运行」填写输入；在「运行监控」里看节点着色、时间线和指标，并在人工检查点批准、反馈或终止。模板可以「复制为我的 Loop」后拖拽修改。
5. **扩展能力**：「技能」→「发现」联网建立索引后检索安装；「MCP」添加精选服务器或从官方注册表检索。

**本地模型（Ollama）**：选「Ollama (本地)」预设即可，走 Ollama 原生接口（`http://localhost:11434`）。「上下文长度」会作为 `num_ctx` 传给 Ollama；它的 OpenAI 兼容接口（`/v1`）固定只有 4096，会截断 Agent 的系统提示（约 5k tokens），所以不要用那个接口。上下文越大越占内存：8 GB 内存的电脑建议 8192，16 GB 以上可用 16384 或更大。模型是否支持工具调用、思考、图像会从 Ollama 自动读取，「测试连接」会显示结果。模型需要支持工具调用（如 `qwen3`）；0.6B–2B 的小模型只适合验证流程，做真正的研究建议 7B 以上或云端模型。

外部服务密钥（「设置 → 服务密钥」）都是可选的：Semantic Scholar（减少 429 限流）、OpenAlex（2026 年起需要 Key，按量计费）、NCBI（提高 PubMed 限速）、Tavily / Brave（给非 Claude 模型提供网页搜索）、GitHub Token（提高技能下载限额）。「设置 → 通用」中的联系邮箱只会发给 Crossref 与 NCBI，用于它们的礼貌访问池。

---

## 命令行 `a4s`

```bash
uv run a4s --help
uv run a4s doctor                                  # 检查 Python、uv、git、Node、钥匙串、模型与密钥
uv run a4s models add --preset anthropic --model claude-opus-5
uv run a4s init ~/Agent4Sci/theta-wm --name "海马 theta 与工作记忆" --domain neuro --venv
cd ~/Agent4Sci/theta-wm
uv run a4s chat "检索近五年海马 theta 与工作记忆的实验研究"   # 单条消息；省略参数进入交互模式
uv run a4s chat --temporary                         # 临时对话：不保存、不读写记忆
uv run a4s run lit-review -i "question=海马 theta 与工作记忆"  # 检查点在终端里回答
uv run a4s run reflect-refine --auto -i "task=写一段综述引言"   # --auto：全自动，跳过检查点
uv run a4s status                                   # 最近的运行；a4s status <run_id> 看节点
uv run a4s resume <run_id>                          # 从断点继续
uv run a4s rollback                                 # 列出工作区提交；a4s rollback <sha> 回滚
uv run a4s skills index && uv run a4s skills search eeg
uv run a4s skills install K-Dense-AI/scientific-agent-skills --select neurokit2
uv run a4s mcp add mne --catalog                    # 从精选列表添加并测试
uv run a4s memory list
```

---

## 开发

```bash
uv sync                       # Python 依赖（含开发依赖）
uv run pytest -q              # 后端测试（离线；live / network 标记的测试默认跳过）
cd desktop && npm install
npm run dev                   # Electron 开发模式（自动用 uv run 启动后端）
npm run typecheck && npm test # TypeScript 检查与前端单元测试
```

浏览器模式（便于调试界面）：

```bash
uv run a4s serve --demo --port 8765 --token dev
npm --prefix desktop run web
# 打开 http://localhost:5178/?api=http://127.0.0.1:8765&token=dev
```

在已安装的应用旁试用新构建：把 dmg 里的 Agent4Sci.app 放到“应用程序”以外的任意位置，再用单独的界面配置和数据目录启动，不影响正在用的应用：

```bash
A4S_USER_DATA_DIR=/tmp/a4s-try/ui A4S_DATA_DIR=/tmp/a4s-try/data <新构建>/Agent4Sci.app/Contents/MacOS/Agent4Sci
```

界面文字以中文为源语言（中文字符串即 i18n 的 key），英文翻译在 `desktop/src/renderer/src/i18n/en.ts`。新增界面文字后运行 `uv run python desktop/scripts/i18n_check.py` 列出尚未翻译的字符串。应用图标由 `uv run python packaging/make_icons.py` 生成（纯 Python 的距离场渲染，无 Pillow 依赖）。构建脚本只在 `desktop/build/icon.*` 缺失时才生成，所以**改了图标设计后要手动跑一次**，否则会沿用旧图标；`--variant` 切换图形，`--preview DIR` 只出预览图和各尺寸对照表，不碰正式资源。

### 目录结构

```
src/agent4sci/
  core/      数据目录、SQLite、钥匙串、模型注册表、事件总线、项目
  llm/       Anthropic / OpenAI(兼容) 适配器、演示模型、价格、连接测试
  agent/     Agent 主循环、Harness 档案与构建、审批 / 提问、预算、上下文压缩
  tools/     文件、命令行与后台任务、Python 内核、网页、文献、参考文献、发现账本、神经数据、数字溯源
  memory/    记忆存储与整理
  skills/    SKILL.md 解析、发现与激活、安装器、锁文件、许可证、安全扫描、目录与索引、技能沉淀
  mcp/       MCP 客户端管理与官方注册表
  loops/     图模型与校验、表达式、执行引擎、检查、锦标赛、git、模板与角色提示
  domains/   领域包（通用、AI/ML、生命科学/化学/材料、神经科学）
  server/    FastAPI 后端（HTTP + WebSocket）
  cli.py     a4s 命令行
desktop/     Electron + React 桌面应用
packaging/   PyInstaller 配置、uv 下载、图标生成、一键构建脚本
tests/       pytest
```

### 数据位置

| 内容 | macOS | Windows |
|---|---|---|
| 数据库、全局记忆、全局技能、背景素材、日志 | `~/Library/Application Support/agent4sci` | `%LOCALAPPDATA%\agent4sci` |
| 桌面端界面数据与后端启动日志 | `~/Library/Application Support/Agent4Sci/desktop` | `%APPDATA%\Agent4Sci\desktop` |
| 默认项目目录 | `~/Agent4Sci` | `%USERPROFILE%\Agent4Sci` |
| API Key 与服务密钥 | 钥匙串 | Windows 凭据管理器 |

环境变量：`A4S_DATA_DIR`（数据目录）、`A4S_PROJECTS_DIR`（默认项目目录）、`A4S_SECRETS=keyring|file|memory`（密钥存储）、`A4S_FAKE_LLM=1`（演示模型）、`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY`（CLI 会自动建立对应模型档案）。

---

## 自检、备份与迁移

- **成果自检**（项目 → 自检）：随时检查这个项目是否经得起看——引用的文献键是否都在 `references.bib` 里、结果章节的数字是否有出处、占位符是否都渲染了、必需产物是否齐全，并把发现账本里的每条证据**按当前文件重新核验一遍**（记录时通过、现在不通过的会单独列为错误）。只报告，不拦运行；报告存在项目的 `.a4s/audit/` 下。
- **备份与恢复**（设置 → 关于）：把数据库、记忆、技能、领域包打成一个 zip。项目文件夹不在内（它们有自己的 git 历史），背景图片与视频也不在内（太大）。API Key 存在系统钥匙串里，不进备份，恢复后需要重填——界面会列出是哪些模型。
- **配置包**（设置 → 关于）：导出模型档案（不含 Key）、Harness、Loop、领域包、MCP 配置与角色映射，拿到另一台机器或给同事导入；同名项默认跳过，按住 Shift 导入则覆盖。
- **环境诊断**（设置 → 关于）：`a4s doctor` 的检查项在界面里也能跑（打包版不含命令行，这是唯一的自查入口），可选同时测 arXiv / Crossref / GitHub 的连通性。

## 安全说明

- 后端只监听 `127.0.0.1` 的随机端口，每次启动生成随机 token；渲染进程开启 `sandbox` 与 `contextIsolation`，CSP 只允许连接本地后端，外链用系统浏览器打开。
- **权限模式**（对话顶部可切，Harness 里设默认）：
  - **总是询问**：写文件、跑命令、装技能、联网都要你点同意；读取和检索不打扰你。
  - **按需提问**（默认）：项目内的事直接做；命令伸到项目外、安装外部代码、或命中危险命令时才问你。
  - **全部允许**：什么都不问，连危险命令拦截也关掉——只在无人值守且信任该流程时用。
  - **自定义**：按工具类别（文件写入 / 命令行 / 网页 / 记忆 / MCP / 子代理…）逐项设自动、询问或禁止。
- 命令行工具运行时会屏蔽名字里含 KEY / TOKEN / SECRET 的环境变量（项目设置里可指定透传）；文件工具限定在工作区内，终端不受此限制——这正是「按需提问」会在命令引用项目外路径时拦一下的原因。
- Agent 装 Python 包只会装进项目自己的虚拟环境（`.venv`）：虚拟环境之外的 `pip install` 会被拒绝，不会改动系统或 conda 的 Python。
- 第三方技能与 MCP 服务器都是外部代码：技能安装前做许可证检查与安全扫描，未审核技能里的脚本首次执行需要批准；只添加你信任的 MCP 服务器。
- 记忆内容被视为参考笔记而不是指令，疑似注入的写入会被标记。

## 常见问题

- **macOS 提示“Apple 无法验证此 App 是否包含恶意软件”**：未公证的应用会被 Gatekeeper 拦截。先双击一次，再到**系统设置 → 隐私与安全性**点“仍要打开”；macOS 15 之前也可以右键 →“打开”。等效的命令是 `xattr -dr com.apple.quarantine /Applications/Agent4Sci.app`。如果提示的是“已损坏”，那通常是下载不完整或解压时丢了扩展属性，重新下载即可。
- **Windows SmartScreen / Defender 拦截**：未签名的安装包首次运行会被 SmartScreen 提示（“更多信息”→“仍要运行”）。PyInstaller 打包的 `agent4sci-server.exe` 偶尔会被 Defender 误报；可在“Windows 安全中心 → 病毒和威胁防护 → 保护历史记录”中还原，并把安装目录加入排除项。
- **后端启动失败**：窗口会显示错误和日志路径。启动日志在“桌面端界面数据”目录的 `logs/backend.log`（菜单里也有“打开后端日志”）；MCP 服务器各自的输出在数据目录的 `logs/mcp/<名称>.log`。
- **Windows 上命令输出或文件乱码**：命令行工具会让 PowerShell 以 UTF-8 输出和写文件（包括 `>` 重定向），Python 子进程也以 UTF-8 运行；读取结果文件时能识别 UTF-8 BOM 与 UTF-16。如果自己的脚本仍然乱码，请确认文件以 UTF-8 保存。
- **技能下载慢或 GitHub 限流**：未登录时 GitHub API 每小时只有 60 次。在“设置 → 服务密钥”里填 GitHub Token 可提高到每小时 5000 次。大仓库只下载所需技能的文件，不会下载整个仓库。
- **诊断环境**：在源码目录运行 `uv run a4s doctor`，会检查 Python、uv、git、Node、钥匙串、模型与服务密钥（加 `--network` 同时检查网络）。安装包里只包含后端服务，不包含 `a4s` 命令行。

## 许可证

MIT，见 [LICENSE](LICENSE)。软件的能力边界、数据去向与免责声明写在应用内的**设置 → 协议与声明**里（首次启动会要求同意一次）。
