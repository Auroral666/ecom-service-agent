const partsFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

/** Business timestamps without an offset already represent the store's local date. */
export function formatDate(value?: string | null): string {
  if (!value) return '未提供'
  const local = /^(\d{4})-(\d{2})-(\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$/.exec(value)
  if (local) {
    const [, year, month, day] = local
    const check = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)))
    if (check.getUTCFullYear() !== Number(year) || check.getUTCMonth() + 1 !== Number(month) || check.getUTCDate() !== Number(day)) return '未提供'
    return year + '年' + month + '月' + day + '日'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未提供'
  const parts = Object.fromEntries(partsFormatter.formatToParts(date).map(part => [part.type, part.value]))
  return parts.year + '年' + parts.month + '月' + parts.day + '日'
}

export function formatDateTime(value?: string | null): string {
  const date = formatDate(value)
  if (!value || date === '未提供') return date
  const local = /^\d{4}-\d{2}-\d{2}[ T](\d{2}:\d{2})/.exec(value)
  if (local && !/[Zz]|[+-]\d{2}:?\d{2}$/.test(value)) return date + ' ' + local[1]
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return date
  const parts = Object.fromEntries(partsFormatter.formatToParts(parsed).map(part => [part.type, part.value]))
  return date + ' ' + parts.hour + ':' + parts.minute
}
