import { contextBridge, ipcRenderer, webUtils } from 'electron'

const backend = ipcRenderer.sendSync('a4s:backend-info') as { url: string; token: string } | null

contextBridge.exposeInMainWorld('a4s', {
  backend,
  platform: process.platform,
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('a4s:pick-folder'),
  pickFile: (kind: 'media'): Promise<string | null> => ipcRenderer.invoke('a4s:pick-file', kind),
  pathForFile: (file: File): string | null => {
    try {
      return webUtils.getPathForFile(file) || null
    } catch {
      return null
    }
  },
  openPath: (path: string): Promise<string> => ipcRenderer.invoke('a4s:open-path', path),
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke('a4s:open-external', url),
  setNativeTheme: (mode: 'light' | 'dark' | 'system'): void => ipcRenderer.send('a4s:theme', mode),
  restartBackend: (): Promise<{ url: string; token: string }> => ipcRenderer.invoke('a4s:backend-restart'),
  onBackendExited: (listener: (code: number | null) => void): void => {
    ipcRenderer.on('a4s:backend-exited', (_event, code) => listener(code))
  },
})
