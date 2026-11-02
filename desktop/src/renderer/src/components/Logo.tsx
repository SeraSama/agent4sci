// The app's own mark — the same node-graph A the installer icon is drawn from (packaging/make_icons.py),
// so the sidebar, the Dock and the installer all agree. The SDF renderer gets its glow from the distance
// field; here two low-opacity strokes under the mark approximate it, which is plenty at 20px.
export function Logo({ className }: { className?: string }) {
  const mark = 'M50 26L29 74M50 26L71 74M35.8 58.5L64.2 58.5'
  return (
    <svg viewBox="0 0 100 100" className={className} role="img" aria-label="Agent4Sci">
      <defs>
        <linearGradient id="a4s-tile" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#120d28" />
          <stop offset="1" stopColor="#241747" />
        </linearGradient>
        <linearGradient id="a4s-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#e0e2fe" />
          <stop offset="1" stopColor="#fcdcfe" />
        </linearGradient>
        <radialGradient id="a4s-lift" cx="0.5" cy="0.44" r="0.55">
          <stop offset="0" stopColor="#5b21b6" stopOpacity="0.55" />
          <stop offset="1" stopColor="#5b21b6" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect x="10" y="10" width="80" height="80" rx="18" fill="url(#a4s-tile)" />
      <rect x="10" y="10" width="80" height="80" rx="18" fill="url(#a4s-lift)" />
      <g transform="translate(50 50) scale(1.08) translate(-50 -50)">
        <g stroke="#a855f7" fill="#a855f7" strokeLinecap="round" opacity="0.4">
          <path d={mark} strokeWidth="13" fill="none" />
          <circle cx="50" cy="26" r="12.5" />
          <circle cx="29" cy="74" r="12.5" />
          <circle cx="71" cy="74" r="12.5" />
        </g>
        <g stroke="url(#a4s-mark)" fill="url(#a4s-mark)" strokeLinecap="round">
          <path d={mark} strokeWidth="6" fill="none" />
          <circle cx="50" cy="26" r="9" />
          <circle cx="29" cy="74" r="9" />
          <circle cx="71" cy="74" r="9" />
          <circle cx="50" cy="58.5" r="5.2" />
        </g>
      </g>
    </svg>
  )
}
