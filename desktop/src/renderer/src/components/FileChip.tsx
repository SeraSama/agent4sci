// An attached file, shown by what it is: a coloured tile per file family, the name, and its size.
import { Database, File, FileArchive, FileAudio, FileCode, FileImage, FileSpreadsheet, FileText, FileVideo,
  Presentation, TriangleAlert, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Marquee } from '@/components/Marquee'
import { Tip } from '@/components/ui'
import { fileKind, KIND_COLOR, KIND_LABEL, type FileKind } from '@/lib/filetypes'
import { cn, formatBytes } from '@/lib/utils'

const ICONS: Record<FileKind, typeof File> = {
  doc: FileText, pdf: FileText, code: FileCode, image: FileImage, sheet: FileSpreadsheet,
  slides: Presentation, video: FileVideo, audio: FileAudio, data: Database, archive: FileArchive, other: File,
}

export function FileChip({ name, path, size, missing, onRemove }: {
  name: string
  path: string
  size?: number
  missing?: boolean
  onRemove?: () => void
}) {
  const { t } = useTranslation()
  const kind = fileKind(name)
  const Icon = missing ? TriangleAlert : ICONS[kind]
  return (
    <Tip content={missing ? t('找不到这个路径：{{p}}', { p: path }) : path}>
      <span className={cn('group inline-flex max-w-[15rem] items-center gap-2 rounded-lg border py-1 pr-1 pl-1.5 text-xs transition',
        missing ? 'border-danger/40 bg-danger/6' : 'border-border bg-surface hover:border-primary/40')}>
        <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-md',
          missing ? 'bg-danger/12 text-danger' : KIND_COLOR[kind])}>
          <Icon className="h-3.5 w-3.5" />
        </span>
        <span className="flex min-w-0 flex-col leading-tight">
          <Marquee className={cn('font-medium', missing && 'text-danger')}>{name}</Marquee>
          <span className="truncate text-[10px] text-subtle">
            {missing ? t('路径不存在') : size !== undefined ? `${t(KIND_LABEL[kind])} · ${formatBytes(size)}` : t(KIND_LABEL[kind])}
          </span>
        </span>
        {onRemove && (
          <button onClick={onRemove} aria-label={t('移除')}
            className="shrink-0 rounded p-1 text-subtle opacity-0 transition group-hover:opacity-100 hover:bg-surface-2 hover:text-fg">
            <X className="h-3 w-3" />
          </button>
        )}
      </span>
    </Tip>
  )
}
