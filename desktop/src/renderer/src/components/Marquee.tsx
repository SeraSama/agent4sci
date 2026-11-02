// Text that scrolls itself when it does not fit, instead of being cut off with an ellipsis the user
// can never read past. Stays still (and stays readable) when it fits, or when the OS asks for less motion.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

const SPEED = 26 // px per second — slow enough to read

export function Marquee({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
  const outer = useRef<HTMLSpanElement>(null)
  const inner = useRef<HTMLSpanElement>(null)
  const [overflow, setOverflow] = useState(0)

  const measure = useCallback(() => {
    const box = outer.current
    const text = inner.current
    if (!box || !text) return
    setOverflow(Math.max(0, Math.ceil(text.scrollWidth - box.clientWidth)))
  }, [])

  useEffect(() => {
    measure()
    if (!outer.current || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(outer.current)
    if (inner.current) observer.observe(inner.current)
    return () => observer.disconnect()
  }, [measure, children])

  return (
    <span ref={outer} title={title} className={cn('block min-w-0 overflow-hidden whitespace-nowrap', className)}>
      <span
        ref={inner}
        className={cn('inline-block max-w-none align-bottom', overflow > 0 && 'a4s-marquee')}
        style={overflow > 0
          ? ({ '--a4s-shift': `${-overflow}px`, '--a4s-marquee-time': `${(overflow / SPEED) * 2 + 4}s` } as React.CSSProperties)
          : undefined}
      >
        {children}
      </span>
    </span>
  )
}
