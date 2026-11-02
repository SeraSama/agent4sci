// The user's background image or video, painted behind a region of the window.
import { useEffect, useRef } from 'react'
import { assetUrl } from '@/lib/api'
import { coversRegion, type Region } from '@/lib/uiprefs'
import { useUi } from '@/lib/uistore'
import { cn } from '@/lib/utils'

/** Paint the background inside this region. Renders nothing unless the user picked one for it. */
export function BackgroundLayer({ region }: { region: Region }) {
  const bg = useUi((s) => s.ui.background)
  const video = useRef<HTMLVideoElement>(null)

  // A looping video keeps the GPU busy; stop it whenever the window is not being looked at.
  useEffect(() => {
    const el = video.current
    if (!el) return
    const sync = () => {
      if (document.hidden || !document.hasFocus()) el.pause()
      else void el.play().catch(() => undefined)
    }
    sync()
    document.addEventListener('visibilitychange', sync)
    window.addEventListener('focus', sync)
    window.addEventListener('blur', sync)
    return () => {
      document.removeEventListener('visibilitychange', sync)
      window.removeEventListener('focus', sync)
      window.removeEventListener('blur', sync)
    }
  }, [bg.asset, bg.kind])

  if (!bg.asset || bg.region !== region) return null
  const url = assetUrl(bg.asset)
  const filter = bg.blur > 0 ? `blur(${bg.blur}px)` : undefined

  return (
    <div className={cn('pointer-events-none overflow-hidden', region === 'window' ? 'fixed inset-0 z-0' : 'absolute inset-0 z-0')}
      aria-hidden>
      {bg.kind === 'video' ? (
        <video
          ref={video}
          key={bg.asset}
          src={url}
          autoPlay
          loop
          muted
          playsInline
          // A blur filter has to over-scale, or the softened edges show as a bright rim.
          className={cn('h-full w-full', bg.fit === 'contain' ? 'object-contain' : 'object-cover')}
          style={{ opacity: bg.opacity, filter, transform: bg.blur > 0 ? 'scale(1.06)' : undefined }}
        />
      ) : (
        <div
          className="h-full w-full"
          style={{
            opacity: bg.opacity,
            filter,
            transform: bg.blur > 0 ? 'scale(1.06)' : undefined,
            backgroundImage: `url("${url}")`,
            backgroundSize: bg.fit === 'tile' ? 'auto' : bg.fit,
            backgroundRepeat: bg.fit === 'tile' ? 'repeat' : 'no-repeat',
            backgroundPosition: 'center',
          }}
        />
      )}
      {bg.dim > 0 && <div className="absolute inset-0 bg-bg" style={{ opacity: bg.dim }} />}
    </div>
  )
}

/** Panes above a background go transparent so it shows through; cards inside them stay opaque. */
export function usePaneTransparent(region: Exclude<Region, 'window'>): boolean {
  return coversRegion(useUi((s) => s.ui.background), region)
}
