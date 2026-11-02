// A right-click menu. Hand-rolled rather than @radix-ui/react-context-menu so no new dependency is
// needed: the trade-off is less screen-reader polish (keyboard navigation is here, roles are thinner).
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/utils'

export interface MenuItem {
  label: ReactNode
  icon?: ReactNode
  onSelect?: () => void
  danger?: boolean
  disabled?: boolean
  separatorBefore?: boolean
}

interface Position { x: number; y: number }

/** Attach to any element: `<div {...useContextMenu(items).trigger}>`. */
export function useContextMenu(items: MenuItem[] | (() => MenuItem[])) {
  const [at, setAt] = useState<Position | null>(null)
  const list = typeof items === 'function' ? items : () => items
  return {
    trigger: {
      onContextMenu: (e: React.MouseEvent) => {
        e.preventDefault()
        e.stopPropagation()
        setAt({ x: e.clientX, y: e.clientY })
      },
    },
    menu: at ? <ContextMenu at={at} items={list()} onClose={() => setAt(null)} /> : null,
  }
}

const MARGIN = 8

export function ContextMenu({ at, items, onClose }: { at: Position; items: MenuItem[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<Position>(at)
  const [active, setActive] = useState(-1)
  const usable = items.filter((i) => !i.disabled)

  // Flip into view once the real size is known.
  useEffect(() => {
    const box = ref.current?.getBoundingClientRect()
    if (!box) return
    setPos({
      x: Math.max(MARGIN, Math.min(at.x, window.innerWidth - box.width - MARGIN)),
      y: Math.max(MARGIN, Math.min(at.y, window.innerHeight - box.height - MARGIN)),
    })
    ref.current?.focus()
  }, [at.x, at.y])

  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    // `capture` so a click that also triggers an app handler still closes the menu.
    document.addEventListener('mousedown', away, true)
    document.addEventListener('contextmenu', away, true)
    window.addEventListener('resize', onClose)
    window.addEventListener('blur', onClose)
    document.addEventListener('scroll', onClose, true)
    return () => {
      document.removeEventListener('mousedown', away, true)
      document.removeEventListener('contextmenu', away, true)
      window.removeEventListener('resize', onClose)
      window.removeEventListener('blur', onClose)
      document.removeEventListener('scroll', onClose, true)
    }
  }, [onClose])

  const pick = (item: MenuItem) => {
    onClose()
    item.onSelect?.()
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') return onClose()
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!usable.length) return
      const step = e.key === 'ArrowDown' ? 1 : -1
      setActive((i) => (i + step + usable.length) % usable.length)
    }
    if ((e.key === 'Enter' || e.key === ' ') && usable[active]) {
      e.preventDefault()
      pick(usable[active])
    }
  }

  return createPortal(
    <div
      ref={ref}
      role="menu"
      tabIndex={-1}
      onKeyDown={onKeyDown}
      style={{ left: pos.x, top: pos.y }}
      className="fixed z-[70] min-w-[172px] overflow-hidden rounded-lg border border-border bg-elevated py-1 shadow-2xl outline-none"
    >
      {items.map((item, i) => {
        const index = usable.indexOf(item)
        return (
          <div key={i}>
            {item.separatorBefore && i > 0 && <div className="my-1 h-px bg-border" />}
            <button
              role="menuitem"
              disabled={item.disabled}
              onMouseEnter={() => setActive(index)}
              onClick={() => pick(item)}
              className={cn('flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition disabled:opacity-40',
                item.danger ? 'text-danger' : 'text-fg',
                index >= 0 && index === active && !item.disabled && (item.danger ? 'bg-danger/12' : 'bg-surface-2'))}
            >
              {item.icon && <span className="shrink-0 text-muted">{item.icon}</span>}
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
            </button>
          </div>
        )
      })}
    </div>,
    document.body,
  )
}
