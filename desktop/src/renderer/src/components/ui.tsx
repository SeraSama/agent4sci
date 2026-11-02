import * as DialogPrimitive from '@radix-ui/react-dialog'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import * as TabsPrimitive from '@radix-ui/react-tabs'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { Loader2, X } from 'lucide-react'
import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode,
  type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

// ---- Button ------------------------------------------------------------------------------

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline'
type Size = 'sm' | 'md' | 'lg' | 'icon'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-primary text-primary-fg hover:brightness-110 active:brightness-95',
  secondary: 'bg-surface-2 text-fg hover:bg-selection/60 border border-border',
  ghost: 'text-fg hover:bg-surface-2',
  danger: 'bg-danger text-white hover:brightness-110',
  outline: 'border border-border text-fg hover:bg-surface-2',
}
const SIZES: Record<Size, string> = {
  sm: 'h-7 px-2.5 text-xs gap-1.5',
  md: 'h-8.5 px-3.5 text-sm gap-2',
  lg: 'h-10 px-5 text-sm gap-2',
  icon: 'h-8 w-8 justify-center',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'secondary', size = 'md', loading, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex shrink-0 select-none items-center rounded-md font-medium whitespace-nowrap transition',
        'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary',
        'disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  ),
)
Button.displayName = 'Button'

// ---- Inputs -------------------------------------------------------------------------------

const field =
  'w-full rounded-md border border-border bg-surface px-2.5 text-sm text-fg placeholder:text-subtle ' +
  'focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/25 disabled:opacity-60'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(field, 'h-8.5', className)} {...props} />,
)
Input.displayName = 'Input'

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => <textarea ref={ref} className={cn(field, 'py-2 leading-relaxed', className)} {...props} />,
)
Textarea.displayName = 'Textarea'

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select ref={ref} className={cn(field, 'h-8.5 cursor-pointer pr-7', className)} {...props}>
      {children}
    </select>
  ),
)
Select.displayName = 'Select'

export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cn('flex flex-col gap-1.5', className)}>
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="text-xs text-subtle">{hint}</span>}
    </label>
  )
}

export function Switch({ checked, onCheckedChange, disabled, label }: { checked: boolean; onCheckedChange: (v: boolean) => void; disabled?: boolean; label?: ReactNode }) {
  return (
    <label className={cn('inline-flex items-center gap-2 text-sm', disabled && 'opacity-60')}>
      <SwitchPrimitive.Root
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        className="relative h-5 w-9 shrink-0 cursor-pointer rounded-full bg-border transition data-[state=checked]:bg-primary"
      >
        <SwitchPrimitive.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-white shadow transition data-[state=checked]:translate-x-[18px]" />
      </SwitchPrimitive.Root>
      {label}
    </label>
  )
}

export function Segmented<T extends string>({ value, options, onChange, size = 'md' }: {
  value: T
  options: { value: T; label: ReactNode }[]
  onChange: (v: T) => void
  size?: 'sm' | 'md'
}) {
  return (
    <div className="inline-flex rounded-md border border-border bg-surface-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cn(
            'rounded-[calc(var(--a4s-radius)*0.75)] font-medium transition',
            size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1 text-sm',
            value === o.value ? 'bg-surface text-fg shadow-sm' : 'text-muted hover:text-fg',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ---- Display ----------------------------------------------------------------------------------

export function Badge({ children, tone = 'neutral', className }: { children: ReactNode; tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'accent'; className?: string }) {
  const tones = {
    neutral: 'bg-surface-2 text-muted border-border',
    primary: 'bg-primary/12 text-primary border-primary/25',
    success: 'bg-success/12 text-success border-success/25',
    warning: 'bg-warning/12 text-warning border-warning/30',
    danger: 'bg-danger/12 text-danger border-danger/25',
    info: 'bg-info/12 text-info border-info/25',
    accent: 'bg-accent/12 text-accent border-accent/25',
  }
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-full border px-2 py-px text-[11px] font-medium whitespace-nowrap', tones[tone], className)}>
      {children}
    </span>
  )
}

export function Card({ children, className, ...props }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('rounded-lg border border-border bg-surface', className)} {...props}>
      {children}
    </div>
  )
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('h-4 w-4 animate-spin text-muted', className)} />
}

export function Empty({ icon, title, hint, action }: { icon?: ReactNode; title: ReactNode; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      {icon && <div className="text-subtle">{icon}</div>}
      <div className="text-sm font-medium">{title}</div>
      {hint && <div className="max-w-md text-xs leading-relaxed text-muted">{hint}</div>}
      {action}
    </div>
  )
}

export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border px-6 pt-5 pb-4">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-xs text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

export function SectionTitle({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <h2 className="text-xs font-semibold tracking-wide text-muted uppercase">{children}</h2>
      {actions}
    </div>
  )
}

// ---- Tooltip -----------------------------------------------------------------------------------

export const TooltipProvider = TooltipPrimitive.Provider

export function Tip({ content, children, side = 'top' }: { content: ReactNode; children: ReactNode; side?: 'top' | 'right' | 'bottom' | 'left' }) {
  return (
    <TooltipPrimitive.Root delayDuration={250}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content side={side} sideOffset={6}
          className="z-50 max-w-xs rounded-md border border-border bg-elevated px-2 py-1 text-xs text-fg shadow-lg">
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}

// ---- Dialog -------------------------------------------------------------------------------------

export function Dialog({ open, onOpenChange, title, description, children, footer, width = 'max-w-lg',
  layer = 'base' }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  width?: string
  /** 'top' stacks above another open dialog — used by the shared confirmation. */
  layer?: 'base' | 'top'
}) {
  const { t } = useTranslation()
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className={cn('fixed inset-0 bg-black/40 backdrop-blur-[2px]', layer === 'top' ? 'z-[64]' : 'z-40')} />
        <DialogPrimitive.Content
          className={cn('fixed top-1/2 left-1/2 flex max-h-[85vh] w-[92vw] -translate-x-1/2 -translate-y-1/2 flex-col',
            layer === 'top' ? 'z-[65]' : 'z-50',
            'rounded-xl border border-border bg-elevated shadow-2xl', width)}
        >
          <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold">{title}</DialogPrimitive.Title>
              {description && <DialogPrimitive.Description className="mt-1 text-xs text-muted">{description}</DialogPrimitive.Description>}
            </div>
            <DialogPrimitive.Close className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-fg" aria-label={t('关闭')}>
              <X className="h-4 w-4" />
            </DialogPrimitive.Close>
          </div>
          {children != null && <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>}
          {footer && <div className="flex justify-end gap-2 border-t border-border px-5 py-3">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

// ---- Tabs -----------------------------------------------------------------------------------------

export function Tabs({ value, onValueChange, tabs, children, className }: {
  value: string
  onValueChange: (v: string) => void
  tabs: { value: string; label: ReactNode }[]
  children: ReactNode
  className?: string
}) {
  return (
    <TabsPrimitive.Root value={value} onValueChange={onValueChange} className={cn('flex min-h-0 flex-col', className)}>
      <TabsPrimitive.List className="flex gap-1 border-b border-border px-6">
        {tabs.map((tab) => (
          <TabsPrimitive.Trigger key={tab.value} value={tab.value}
            className="-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted transition hover:text-fg data-[state=active]:border-primary data-[state=active]:text-fg">
            {tab.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {children}
    </TabsPrimitive.Root>
  )
}

export const TabPanel = ({ value, children, className }: { value: string; children: ReactNode; className?: string }) => (
  <TabsPrimitive.Content value={value} className={cn('min-h-0 flex-1 overflow-y-auto outline-none', className)}>
    {children}
  </TabsPrimitive.Content>
)

// ---- Misc ---------------------------------------------------------------------------------------------

export function ColorField({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: ReactNode }) {
  return (
    <label className="flex items-center gap-2 rounded-md border border-border bg-surface px-2 py-1.5">
      <input type="color" value={value} onChange={(e) => onChange(e.target.value)}
        className="h-6 w-7 shrink-0 cursor-pointer rounded border border-border bg-transparent p-0" />
      <span className="min-w-0 flex-1 truncate text-xs">{label}</span>
      <input value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false}
        className="w-[76px] bg-transparent font-mono text-[11px] text-muted outline-none" />
    </label>
  )
}

export function Slider({ value, onChange, min = 0, max = 1, step = 0.05, label, format, className }: {
  value: number
  onChange: (v: number) => void
  min?: number
  max?: number
  step?: number
  label?: ReactNode
  format?: (v: number) => string
  className?: string
}) {
  return (
    <label className={cn('flex items-center gap-3 text-xs', className)}>
      {label && <span className="w-20 shrink-0 text-muted">{label}</span>}
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1.5 min-w-0 flex-1 cursor-pointer appearance-none rounded-full bg-border accent-[var(--a4s-primary)]" />
      <span className="w-12 shrink-0 text-right font-mono text-subtle">{format ? format(value) : value}</span>
    </label>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-border bg-surface-2 px-1.5 py-px font-mono text-[10px] text-muted">{children}</kbd>
}
