import * as childProcess from 'node:child_process'
import { beforeEach, describe, expect, it, vi } from 'vitest'

interface FakeProc {
  exitCode: number | null
  exit: (code: number | null) => void
}

interface Control {
  spawned: FakeProc[]
  failNext: boolean
}

vi.mock('electron', async () => {
  const { mkdtempSync } = await import('node:fs')
  const { tmpdir } = await import('node:os')
  const { join } = await import('node:path')
  const dir = mkdtempSync(join(tmpdir(), 'a4s-backend-test-'))
  return { app: { getPath: () => dir, getAppPath: () => dir, isPackaged: false } }
})

// A backend process that prints the ready line (or dies first when failNext is set).
vi.mock('node:child_process', async () => {
  const { EventEmitter } = await import('node:events')
  const { PassThrough } = await import('node:stream')
  const control: Control = { spawned: [], failNext: false }

  class Proc extends EventEmitter implements FakeProc {
    stdout = new PassThrough()
    stderr = new PassThrough()
    stdin = new PassThrough()
    exitCode: number | null = null
    pid = 1000 + control.spawned.length

    exit(code: number | null): void {
      if (this.exitCode !== null) return
      this.exitCode = code ?? 0
      this.emit('exit', code)
    }

    kill(): boolean {
      this.exit(null)
      return true
    }
  }

  const spawn = vi.fn(() => {
    const proc = new Proc()
    const fail = control.failNext
    control.failNext = false
    control.spawned.push(proc)
    setTimeout(() => {
      if (fail) proc.exit(1)
      else proc.stdout.write(JSON.stringify({ event: 'ready', port: 4000 + control.spawned.length }) + '\n')
    }, 5)
    return proc
  })
  return { spawn, spawnSync: vi.fn(), __control: control }
})

const control = (): Control => (childProcess as unknown as { __control: Control }).__control

describe('backend supervisor', () => {
  beforeEach(() => {
    vi.resetModules()
    control().spawned.length = 0
    control().failNext = false
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true })))
  })

  it('shares one start between overlapping callers', async () => {
    const backend = await import('../backend')
    const [a, b] = await Promise.all([backend.startBackend(), backend.startBackend()])
    expect(control().spawned).toHaveLength(1)
    expect(b).toBe(a)
    expect(await backend.startBackend()).toBe(a)
    expect(control().spawned).toHaveLength(1)
    backend.stopBackend()
  })

  it('reports crashes of the running backend, not failed starts or stops', async () => {
    const backend = await import('../backend')
    const exits: (number | null)[] = []
    backend.onBackendExit((code) => exits.push(code))

    await backend.startBackend()
    control().spawned[0].exit(3) // crash
    expect(exits).toEqual([3])

    control().failNext = true
    await expect(backend.startBackend()).rejects.toThrow('后端进程退出')
    expect(exits).toEqual([3])

    const info = await backend.startBackend() // restart after the crash
    expect(control().spawned).toHaveLength(3)
    expect(backend.backendInfo()).toBe(info)

    backend.stopBackend()
    control().spawned[2].exit(0)
    expect(exits).toEqual([3])
    expect(backend.backendInfo()).toBeNull()
  })
})
