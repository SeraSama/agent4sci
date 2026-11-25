import { Check, Copy } from 'lucide-react'
import { memo, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { useTranslation } from 'react-i18next'
import { useCopy } from '@/components/CopyButton'
import { cn } from '@/lib/utils'

function openExternal(href: string) {
  if (window.a4s) void window.a4s.openExternal(href)
  else window.open(href, '_blank', 'noopener')
}

/** A code block with a copy button in the corner — the text comes from the rendered node, so it is
 *  exactly what is on screen, highlighting spans and all stripped out by textContent. */
function CodeBlock({ children, ...props }: React.ComponentProps<'pre'>) {
  const { t } = useTranslation()
  const ref = useRef<HTMLPreElement>(null)
  const { copied, copy } = useCopy()
  const Icon = copied ? Check : Copy
  return (
    <div className="group/code relative">
      <pre ref={ref} {...props}>{children}</pre>
      <button
        type="button"
        title={copied ? t('已复制') : t('复制代码')}
        onClick={() => void copy(ref.current?.textContent ?? '')}
        className={cn('absolute top-1.5 right-1.5 rounded border border-border bg-surface/90 p-1 opacity-0',
          'backdrop-blur transition group-hover/code:opacity-100 hover:text-fg',
          copied ? 'text-success opacity-100' : 'text-subtle')}
      >
        <Icon className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

export const Markdown = memo(function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn('a4s-prose', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, [rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          pre: CodeBlock,
          a: ({ href, children }) => (
            <a href={href} onClick={(e) => {
              if (href && /^https?:/.test(href)) {
                e.preventDefault()
                openExternal(href)
              }
            }}>
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
})
