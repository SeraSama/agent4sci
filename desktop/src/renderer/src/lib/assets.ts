// Importing a local image or video into the app's own asset folder. The Electron / browser-mode
// split lives here so the background settings and the avatar picker do not each reinvent it.
export interface Asset {
  id: string
  name: string
  kind: 'image' | 'video'
  size: number
  created: number
  heavy?: boolean
}

/** Ask for a media file and return its path, or null when the user cancels. */
export async function pickMediaPath(promptForPath: () => Promise<string | null>): Promise<string | null> {
  return window.a4s?.pickFile ? window.a4s.pickFile('media') : promptForPath()
}
