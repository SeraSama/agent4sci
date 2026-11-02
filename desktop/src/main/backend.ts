// Starts and supervises the Python backend (agent4sci-server).
// Packaged app: resources/backend/agent4sci-server[.exe] (PyInstaller onedir).
// Development: `uv run agent4sci-server` from the repository root.
import { spawn, spawnSync, type ChildProcess } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import fs from 'node:fs'
import { join } from 'node:path'
import { app } from 'electron'

export interface BackendInfo {
  url: string
  token: string
  logFile: string
}

let child: ChildProcess | null = null
let info: BackendInfo | null = null
let starting: Promise<BackendInfo> | null = null
const exitListeners = new Set<(code: number | null) => void>()

function logFile(): string {
  const dir = join(app.getPath('userData'), 'logs')
  fs.mkdirSync(dir, { recursive: true })
  return join(dir, 'backend.log')
}

function command(): { cmd: string; args: string[]; cwd?: string; env: NodeJS.ProcessEnv } {
  const base = ['--port', '0', '--exit-on-stdin-close']
  if (app.isPackaged) {
    const dir = join(process.resourcesPath, 'backend')
    const exe = process.platform === 'win32' ? 'agent4sci-server.exe' : 'agent4sci-server'
    return { cmd: join(dir, exe), args: base, cwd: dir, env: { ...process.env, A4S_BIN_DIR: join(dir, 'bin') } }
  }
  const args = ['run', 'agent4sci-server', ...base]
  if (process.env.A4S_DEMO === '1') args.push('--demo')
  return { cmd: process.env.A4S_UV || 'uv', args, cwd: join(app.getAppPath(), '..'), env: { ...process.env } }
}

async function waitHealthy(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${url}/api/health`)
      if (res.ok) return
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error('后端健康检查超时')
}

/** Start the backend, or return the running one. Callers that overlap (a Dock click during startup) share one start. */
export function startBackend(): Promise<BackendInfo> {
  if (info && child && child.exitCode === null) return Promise.resolve(info)
  starting ??= launch().finally(() => {
    starting = null
  })
  return starting
}

async function launch(): Promise<BackendInfo> {
  info = null
  const token = randomBytes(24).toString('hex')
  const { cmd, args, cwd, env } = command()
  const file = logFile()
  const log = fs.createWriteStream(file, { flags: 'a' })
  log.write(`\n==== ${new Date().toISOString()} starting ${cmd} ${args.join(' ')}\n`)
  const proc = spawn(cmd, args, {
    cwd,
    env: { ...env, A4S_TOKEN: token, PYTHONUNBUFFERED: '1' },
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
  })
  child = proc
  proc.stderr?.on('data', (d) => log.write(d))
  proc.on('exit', (code) => {
    // Only a crash of the running backend; a failed start is reported by the caller, a stop or restart is intended
    if (child === proc && info !== null) for (const listener of exitListeners) listener(code)
  })
  const port = await new Promise<number>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`后端启动超时，日志：${file}`)), 90_000)
    let buffer = ''
    let ready = false
    proc.stdout?.on('data', (d: Buffer) => {
      log.write(d)
      if (ready) return
      buffer += d.toString()
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? '' // keep the unfinished line
      for (const line of lines) {
        try {
          const msg = JSON.parse(line)
          if (msg.event === 'ready') {
            ready = true
            clearTimeout(timer)
            resolve(msg.port)
            return
          }
        } catch {
          // not JSON
        }
      }
    })
    proc.on('exit', (code) => {
      clearTimeout(timer)
      reject(new Error(`后端进程退出（代码 ${code}），日志：${file}`))
    })
    proc.on('error', (err) => {
      clearTimeout(timer)
      reject(err)
    })
  })
  const url = `http://127.0.0.1:${port}`
  await waitHealthy(url, 30_000)
  info = { url, token, logFile: file }
  return info
}

export function backendInfo(): BackendInfo | null {
  return info
}

/** Called when the running backend exits on its own (a crash), not when it is stopped or fails to start. */
export function onBackendExit(listener: (code: number | null) => void): void {
  exitListeners.add(listener)
}

export function stopBackend(): void {
  const proc = child
  child = null
  info = null
  if (!proc || proc.exitCode !== null) return
  try {
    proc.stdin?.end() // backend exits when stdin closes
  } catch {
    // ignore
  }
  setTimeout(() => {
    if (proc.exitCode !== null) return
    if (process.platform === 'win32' && proc.pid) {
      spawnSync('taskkill', ['/PID', String(proc.pid), '/T', '/F'])
    } else {
      proc.kill('SIGTERM')
    }
  }, 1500)
}
