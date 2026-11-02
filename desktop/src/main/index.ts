import { join } from 'node:path'
import { app, BrowserWindow, dialog, ipcMain, Menu, nativeTheme, shell } from 'electron'
import { backendInfo, onBackendExit, startBackend, stopBackend } from './backend'

// Chromium's profile files get their own folder. On macOS (case-insensitive by default) the default userData
// ~/Library/Application Support/Agent4Sci is the same directory as the backend's …/agent4sci data folder.
// A4S_USER_DATA_DIR (with A4S_DATA_DIR for the backend) runs a second, separate copy, e.g. to try a new build.
app.setPath('userData', process.env.A4S_USER_DATA_DIR || join(app.getPath('appData'), 'Agent4Sci', 'desktop'))

let win: BrowserWindow | null = null
let opening: Promise<void> | null = null
let quitting = false

function allowedExternal(url: string): boolean {
  return /^https?:\/\//i.test(url) || /^mailto:/i.test(url)
}

/** One window at a time: macOS sends `activate` (a Dock click, a relaunch) while the backend is still starting. */
function openWindow(): Promise<void> {
  opening ??= createWindow().finally(() => {
    opening = null
  })
  return opening
}

function fatal(title: string, err: unknown): void {
  dialog.showErrorBox(title, String(err instanceof Error ? err.message : err))
  app.quit()
}

async function createWindow(): Promise<void> {
  try {
    await startBackend()
  } catch (err) {
    fatal('Agent4Sci 后端启动失败', err)
    return
  }

  win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1040,
    minHeight: 680,
    show: false,
    title: 'Agent4Sci',
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#15171c' : '#f6f6f3',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 14, y: 14 },
    autoHideMenuBar: process.platform !== 'darwin',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
    },
  })
  win.on('ready-to-show', () => win?.show())
  win.on('closed', () => {
    win = null
  })
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (allowedExternal(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (event, url) => {
    const devUrl = process.env.ELECTRON_RENDERER_URL
    if (url.startsWith('file:') || (devUrl && url.startsWith(devUrl))) return
    event.preventDefault()
    if (allowedExternal(url)) void shell.openExternal(url)
  })
  if (!app.isPackaged && process.env.ELECTRON_RENDERER_URL) {
    await win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    await win.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// The page reads the backend's address and token when it loads, so after a restart it only needs a reload.
onBackendExit(async (code) => {
  if (quitting) return
  win?.webContents.send('a4s:backend-exited', code)
  const options: Electron.MessageBoxOptions = {
    type: 'error',
    message: 'Agent4Sci 后端意外退出',
    detail: `退出代码：${code ?? '未知'}，原因见“帮助 → 打开后端日志”。正在生成的回复已中断；中断的流程重启后可在“运行”页点“继续运行”。`,
    buttons: ['重启后端', '退出'],
    defaultId: 0,
    cancelId: 1,
  }
  const { response } = win ? await dialog.showMessageBox(win, options) : await dialog.showMessageBox(options)
  if (response !== 0) {
    app.quit()
    return
  }
  try {
    await startBackend()
  } catch (err) {
    fatal('Agent4Sci 后端启动失败', err)
    return
  }
  win?.webContents.reload()
})

function buildMenu(): void {
  const isMac = process.platform === 'darwin'
  const template: Electron.MenuItemConstructorOptions[] = [
    ...(isMac ? [{ role: 'appMenu' as const }] : []),
    { role: 'fileMenu' },
    { role: 'editMenu' },
    {
      label: '视图',
      submenu: [
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    { role: 'windowMenu' },
    {
      label: '帮助',
      submenu: [
        {
          label: '打开后端日志',
          click: () => {
            const info = backendInfo()
            if (info) void shell.openPath(info.logFile)
          },
        },
        { label: '打开数据目录', click: () => void openDataDir() },
      ],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

ipcMain.on('a4s:backend-info', (event) => {
  const info = backendInfo()
  event.returnValue = info ? { url: info.url, token: info.token } : null
})
ipcMain.handle('a4s:pick-folder', async () => {
  const result = await dialog.showOpenDialog({ properties: ['openDirectory', 'createDirectory'] })
  return result.canceled ? null : result.filePaths[0]
})
const FILE_FILTERS: Record<string, Electron.FileFilter[]> = {
  media: [
    { name: '图片与视频', extensions: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'bmp', 'mp4', 'webm', 'm4v', 'mov', 'ogv'] },
    { name: '图片', extensions: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'bmp'] },
    { name: '视频', extensions: ['mp4', 'webm', 'm4v', 'mov', 'ogv'] },
  ],
}
ipcMain.handle('a4s:pick-file', async (_event, kind: string) => {
  const result = await dialog.showOpenDialog({
    properties: ['openFile'],
    filters: FILE_FILTERS[kind] ?? [],
  })
  return result.canceled ? null : result.filePaths[0]
})
ipcMain.handle('a4s:open-path', async (_event, path: string) => shell.openPath(path))
ipcMain.handle('a4s:open-external', async (_event, url: string) => {
  if (allowedExternal(url)) await shell.openExternal(url)
})
ipcMain.on('a4s:theme', (_event, mode: 'light' | 'dark' | 'system') => {
  nativeTheme.themeSource = mode
})
ipcMain.handle('a4s:backend-restart', async () => {
  stopBackend()
  await new Promise((r) => setTimeout(r, 1600))
  const info = await startBackend()
  return { url: info.url, token: info.token }
})

/** The backend owns the data folder (database, memory, skills); ask it where that is. */
async function openDataDir(): Promise<void> {
  const info = backendInfo()
  try {
    if (!info) throw new Error('backend not running')
    const res = await fetch(`${info.url}/api/info`, { headers: { Authorization: `Bearer ${info.token}` } })
    const data = (await res.json()) as { data_dir: string }
    await shell.openPath(data.data_dir)
  } catch {
    await shell.openPath(app.getPath('userData'))
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore()
      win.focus()
    } else if (app.isReady()) {
      void openWindow()
    }
  })
  app.whenReady().then(() => {
    buildMenu()
    void openWindow()
    app.on('activate', () => {
      if (!win) void openWindow()
    })
  })
}

app.on('before-quit', () => {
  quitting = true
  stopBackend()
})
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
