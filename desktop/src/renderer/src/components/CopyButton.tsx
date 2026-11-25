// A small copy button that confirms by turning into a tick for a moment, used under messages,
// on code blocks and anywhere a value is worth lifting out of the page.
import { Check, Copy } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Tip } from '@/components/ui'
import { cn, copyText } from '@/lib/utils'

export function useCopy(): { copied: boolean; copy: (text: string) => Promise<void> } {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
  const copy = async (text: string) => {
    const ok = await copyText(text)
    if (!ok) return
    setCopied(true)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setCopied(false), 1400)
  }
  return { copied, copy }
}

export function CopyButton({ text, label, className, children }: {
  text: string | (() => string)
  label?: string
  className?: string
  children?: React.ReactNode
}) {
  const { t } = useTranslation()
  const { copied, copy } = useCopy()
  const Icon = copied ? Check : Copy
  return (
    <Tip content={copied ? t('已复制') : label ?? t('复制')}>
      <button
        type="button"
        onClick={() => void copy(typeof text === 'function' ? text() : text)}
        className={cn('inline-flex items-center gap-1 rounded px-1.5 py-1 text-[11px] text-subtle transition',
          'hover:bg-surface-2 hover:text-fg', copied && 'text-success', className)}
      >
        <Icon className="h-3.5 w-3.5" />
        {children}
      </button>
    </Tip>
  )
}
