// Small formatting helpers: times, schedules, generated cover colours.

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return ''
  const seconds = Math.round((Date.parse(iso) - now) / 1000)
  const abs = Math.abs(seconds)
  const [value, unit] =
    abs < 60 ? [seconds, 'second'] : abs < 3600 ? [Math.round(seconds / 60), 'minute']
      : abs < 86400 ? [Math.round(seconds / 3600), 'hour'] : [Math.round(seconds / 86400), 'day']
  return new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }).format(value as number, unit as Intl.RelativeTimeFormatUnit)
}

export function clockTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : ''
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return ''
  if (seconds < 60) return `${Math.round(seconds)} s`
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`
}

/** "0 * * * *" -> "Every hour"; other cron expressions are shown as they are. */
export function describeSchedule(cron: string | null | undefined): string {
  const known: Record<string, string> = {
    '0 * * * *': 'Every hour', '@hourly': 'Every hour', '0 0 * * *': 'Every day at midnight', '@daily': 'Every day',
  }
  return cron ? (known[cron] ?? cron) : 'Not scheduled'
}

function hash(text: string): number {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619)
  return h >>> 0
}

/** Two colours for a book's generated cover, stable for the same title. */
export function coverColors(title: string): [string, string] {
  const h = hash(title) % 360
  return [`hsl(${h} 62% 46%)`, `hsl(${(h + 40) % 360} 70% 30%)`]
}
