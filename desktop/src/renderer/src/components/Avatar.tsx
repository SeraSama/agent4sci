// Who is speaking in a chat. The value is "" for the built-in mark, "asset:<id>" for an imported
// image, or any other text (an emoji, one or two letters) drawn in a tinted circle.
import { Sparkles, UserRound } from 'lucide-react'
import { assetUrl } from '@/lib/api'
import { cn } from '@/lib/utils'

export function Avatar({ role, value, className }: {
  role: 'user' | 'assistant'
  value?: string | null
  className?: string
}) {
  const shell = cn('flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-full', className)
  const tint = role === 'user' ? 'bg-surface-2 text-muted' : 'bg-primary/15 text-primary'
  const mark = (value ?? '').trim()

  if (mark.startsWith('asset:')) {
    return <img src={assetUrl(mark.slice(6))} alt="" className={cn(shell, 'object-cover')} />
  }
  if (mark) {
    // Emoji need room; letters look better slightly smaller and tighter.
    const compact = mark.length > 1 && /^[\w\s.-]+$/.test(mark)
    return (
      <span className={cn(shell, tint, compact ? 'text-[10px] font-semibold tracking-tight uppercase' : 'text-sm')}>
        {compact ? mark.slice(0, 2) : [...mark][0]}
      </span>
    )
  }
  const Icon = role === 'user' ? UserRound : Sparkles
  return <span className={cn(shell, tint)}><Icon className="h-3.5 w-3.5" /></span>
}
