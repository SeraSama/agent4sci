// Promise-based confirmation, so `if (!(await confirm(...))) return` reads like the window.confirm it
// replaces. Electron does render window.confirm, but it is a modal OS sheet that ignores the theme and
// cannot show a danger button — and window.prompt is not implemented at all.
import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { create } from 'zustand'
import { Button, Dialog, Input } from '@/components/ui'

export interface ConfirmOptions {
  title: ReactNode
  description?: ReactNode
  confirmLabel?: ReactNode
  cancelLabel?: ReactNode
  danger?: boolean
  /** Ask for a value instead of a yes/no: resolves to the string, or null when cancelled. */
  input?: { label?: ReactNode; placeholder?: string; initial?: string; hint?: ReactNode; required?: boolean }
}

interface Request extends ConfirmOptions {
  id: number
  resolve: (value: boolean | string | null) => void
}

interface State { queue: Request[] }
const useRequests = create<State>(() => ({ queue: [] }))

let nextId = 1

function ask(options: ConfirmOptions): Promise<boolean | string | null> {
  return new Promise((resolve) => {
    const req: Request = { ...options, id: nextId++, resolve }
    useRequests.setState((s) => ({ queue: [...s.queue, req] }))
  })
}

/** Yes/no. Resolves false when dismissed. */
export async function confirmAction(options: ConfirmOptions): Promise<boolean> {
  return (await ask(options)) === true
}

/** Ask for a string. Resolves null when dismissed. */
export async function askForText(options: ConfirmOptions & { input: NonNullable<ConfirmOptions['input']> }): Promise<string | null> {
  const value = await ask(options)
  return typeof value === 'string' ? value : null
}

/** Mounted once in App; renders whichever request is at the head of the queue. */
export function ConfirmHost() {
  const { t } = useTranslation()
  const queue = useRequests((s) => s.queue)
  const current = queue[0]
  const [value, setValue] = useState('')

  useEffect(() => setValue(current?.input?.initial ?? ''), [current?.id])

  if (!current) return null
  const finish = (result: boolean | string | null) => {
    useRequests.setState((s) => ({ queue: s.queue.filter((r) => r.id !== current.id) }))
    current.resolve(result)
  }
  const blocked = !!current.input?.required && !value.trim()
  const accept = () => {
    if (blocked) return
    finish(current.input ? value.trim() : true)
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && finish(current.input ? null : false)}
      title={current.title}
      description={current.description}
      width="max-w-md"
      layer="top"
      footer={
        <>
          <Button variant="ghost" onClick={() => finish(current.input ? null : false)}>
            {current.cancelLabel ?? t('取消')}
          </Button>
          <Button variant={current.danger ? 'danger' : 'primary'} disabled={blocked} onClick={accept}>
            {current.confirmLabel ?? (current.danger ? t('删除') : t('确定'))}
          </Button>
        </>
      }
    >
      {current.input ? (
        <div className="flex flex-col gap-1.5">
          {current.input.label && <span className="text-xs font-medium text-muted">{current.input.label}</span>}
          <Input
            autoFocus
            value={value}
            placeholder={current.input.placeholder}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') accept()
            }}
          />
          {current.input.hint && <span className="text-xs text-subtle">{current.input.hint}</span>}
        </div>
      ) : null}
    </Dialog>
  )
}
