// Short cues synthesised with WebAudio — no audio files, so nothing to ship, license or decode.
import { useUi } from './uistore'

export type Cue = 'start' | 'done' | 'ask' | 'error'

interface Note { hz: number; at: number; dur: number; gain?: number; wave?: OscillatorType }

const CUES: Record<Cue, Note[]> = {
  // A quiet two-note rise: something began.
  start: [{ hz: 523.25, at: 0, dur: 0.1 }, { hz: 783.99, at: 0.09, dur: 0.14 }],
  // Major triad: finished.
  done: [{ hz: 523.25, at: 0, dur: 0.11 }, { hz: 659.25, at: 0.1, dur: 0.11 }, { hz: 783.99, at: 0.2, dur: 0.26 }],
  // Double ping: your turn.
  ask: [{ hz: 880, at: 0, dur: 0.09 }, { hz: 880, at: 0.16, dur: 0.13 }],
  // Falling tritone-ish on a softer wave: something broke.
  error: [{ hz: 392, at: 0, dur: 0.14, wave: 'triangle' }, { hz: 261.63, at: 0.13, dur: 0.3, wave: 'triangle' }],
}

type Ctor = typeof AudioContext
let ctx: AudioContext | null = null

function context(): AudioContext | null {
  const Ctx: Ctor | undefined = window.AudioContext ?? (window as { webkitAudioContext?: Ctor }).webkitAudioContext
  if (!Ctx) return null
  if (!ctx) ctx = new Ctx()
  if (ctx.state === 'suspended') void ctx.resume()
  return ctx
}

/** Play a cue. Respects the user's toggle and volume; silent (never throws) when audio is unavailable. */
export function playCue(cue: Cue, { force = false } = {}): void {
  const { enabled, volume } = useUi.getState().ui.sound
  if (!force && !enabled) return
  if (volume <= 0) return
  try {
    const audio = context()
    if (!audio) return
    const master = audio.createGain()
    master.gain.value = Math.min(1, Math.max(0, volume)) * 0.22  // cues should never be louder than speech
    master.connect(audio.destination)
    const t0 = audio.currentTime + 0.01
    for (const note of CUES[cue]) {
      const osc = audio.createOscillator()
      const env = audio.createGain()
      osc.type = note.wave ?? 'sine'
      osc.frequency.value = note.hz
      // Short attack + exponential decay: a click-free blip.
      env.gain.setValueAtTime(0.0001, t0 + note.at)
      env.gain.exponentialRampToValueAtTime(note.gain ?? 1, t0 + note.at + 0.012)
      env.gain.exponentialRampToValueAtTime(0.0001, t0 + note.at + note.dur)
      osc.connect(env).connect(master)
      osc.start(t0 + note.at)
      osc.stop(t0 + note.at + note.dur + 0.02)
    }
  } catch {
    // no audio device, or the context was blocked before any user gesture
  }
}
