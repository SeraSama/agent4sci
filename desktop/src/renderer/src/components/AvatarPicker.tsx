// Pick what you or a harness look like: an emoji, a couple of letters, or an imported image.
import { ImagePlus, X } from 'lucide-react'
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Avatar } from '@/components/Avatar'
import { askForText } from '@/components/confirm'
import { Button, Input } from '@/components/ui'
import { errorMessage, get, post } from '@/lib/api'
import { pickMediaPath, type Asset } from '@/lib/assets'
import { cn, toast } from '@/lib/utils'

export function AvatarPicker({ role, value, onChange }: {
  role: 'user' | 'assistant'
  value: string
  onChange: (v: string) => void
}) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const { data: assets } = useQuery({ queryKey: ['assets'], queryFn: () => get<Asset[]>('/assets') })
  const images = (assets ?? []).filter((a) => a.kind === 'image')
  const isImage = value.startsWith('asset:')

  const upload = async () => {
    setBusy(true)
    try {
      const path = await pickMediaPath(() => askForText({
        title: t('选择头像图片'),
        input: { label: t('输入图片的完整路径'), required: true },
      }))
      if (!path) return
      const asset = await post<Asset>('/assets', { path })
      await qc.invalidateQueries({ queryKey: ['assets'] })
      onChange(`asset:${asset.id}`)
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Avatar role={role} value={value} className="h-9 w-9" />
        <Input className="max-w-32" value={isImage ? '' : value} placeholder={t('表情或 1–2 个字')}
          onChange={(e) => onChange(e.target.value)} disabled={isImage} />
        <Button size="sm" variant="outline" loading={busy} onClick={upload}>
          <ImagePlus className="h-3.5 w-3.5" />{t('用图片')}
        </Button>
        {value && (
          <Button size="icon" variant="ghost" title={t('恢复默认')} onClick={() => onChange('')}>
            <X className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
      {images.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {images.map((a) => (
            <button key={a.id} title={a.name} onClick={() => onChange(`asset:${a.id}`)}
              className={cn('h-7 w-7 overflow-hidden rounded-full border transition',
                value === `asset:${a.id}` ? 'border-primary ring-2 ring-primary/30' : 'border-border hover:border-primary/50')}>
              <Avatar role={role} value={`asset:${a.id}`} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
