// What kind of file a path is, for the coloured attachment chips. Extension-only: it never touches disk.

export type FileKind = 'doc' | 'pdf' | 'code' | 'image' | 'sheet' | 'slides' | 'video' | 'audio' | 'data' | 'archive' | 'other'

const BY_EXTENSION: Record<string, FileKind> = {}
const register = (kind: FileKind, extensions: string) => {
  for (const ext of extensions.split(' ')) BY_EXTENSION[ext] = kind
}

register('doc', 'txt md markdown rst rtf doc docx odt pages tex bib log')
register('pdf', 'pdf djvu epub')
register('code', 'py ipynb js mjs cjs ts tsx jsx r rmd m mat jl c h cpp cc hpp cs java kt go rs swift rb php lua ' +
  'sh bash zsh ps1 bat sql json jsonl yaml yml toml ini cfg xml html htm css scss vue svelte gradle make cmake')
register('image', 'png jpg jpeg gif webp avif bmp tif tiff svg heic heif ico eps ai psd')
register('sheet', 'csv tsv xls xlsx xlsm ods numbers parquet')
register('slides', 'ppt pptx odp key')
register('video', 'mp4 mov m4v avi mkv webm wmv flv mpg mpeg ogv')
register('audio', 'mp3 wav flac m4a aac ogg oga opus aiff wma')
// The formats this app actually meets in a lab: imaging, electrophysiology, arrays, models.
register('data', 'h5 hdf5 nwb nii gz mgz fif edf bdf set fdt cnt vhdr eeg dcm dicom mgh npy npz pkl pickle ' +
  'mat5 sav dta rdata rds feather arrow db sqlite sqlite3 pt pth ckpt safetensors onnx gguf bin')
register('archive', 'zip tar tgz bz2 xz 7z rar')

/** Longest-suffix match, so `brain.nii.gz` is data rather than an archive. */
export function fileKind(name: string): FileKind {
  const lower = name.toLowerCase()
  const parts = lower.split('.')
  if (parts.length < 2) return 'other'
  if (parts.length > 2) {
    const pair = BY_EXTENSION[`${parts[parts.length - 2]}.${parts[parts.length - 1]}`]
    if (pair) return pair
    // `.nii.gz`, `.tar.gz`: the meaningful extension is the one before the compressor.
    if (parts[parts.length - 1] === 'gz') {
      const inner = BY_EXTENSION[parts[parts.length - 2]]
      if (inner && inner !== 'other') return inner === 'archive' ? 'archive' : inner
      return 'archive'
    }
  }
  return BY_EXTENSION[parts[parts.length - 1]] ?? 'other'
}

/** Tailwind classes for the chip's icon tile: [text colour, tile background]. */
export const KIND_COLOR: Record<FileKind, string> = {
  doc: 'text-blue-500 bg-blue-500/12',
  pdf: 'text-red-500 bg-red-500/12',
  code: 'text-emerald-500 bg-emerald-500/12',
  image: 'text-yellow-500 bg-yellow-500/12',
  sheet: 'text-orange-500 bg-orange-500/12',
  slides: 'text-orange-500 bg-orange-500/12',
  video: 'text-pink-500 bg-pink-500/12',
  audio: 'text-violet-500 bg-violet-500/12',
  data: 'text-cyan-500 bg-cyan-500/12',
  archive: 'text-stone-500 bg-stone-500/12',
  other: 'text-subtle bg-surface-2',
}

export const KIND_LABEL: Record<FileKind, string> = {
  doc: '文档', pdf: 'PDF', code: '代码', image: '图片', sheet: '表格', slides: '幻灯片',
  video: '视频', audio: '音频', data: '数据', archive: '压缩包', other: '文件',
}
