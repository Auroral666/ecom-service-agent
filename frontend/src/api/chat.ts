import axios from 'axios'

export interface StructuredReply { intent: string; confidence: number; reply: string; requires_human: boolean; follow_up_question: string | null }
export interface AgentStep { id: string; type: 'thought' | 'action' | 'observation' | 'route'; title: string; content: string; tool_name?: string | null; tool_args?: Record<string, unknown> | null; tool_result?: string | null; agent_name?: string | null; created_at: string }
export interface ToolEvent { type: 'tool'; id: string; name: string; label: string; source?: string; state: 'waiting' | 'running' | 'succeeded' | 'failed'; summary: string; elapsed_ms?: number; error?: string | null; at_ms?: number }
export interface BusinessBlock { id: string; kind: string; title: string; data: any }
export interface Presentation { request_id?: string; tools: ToolEvent[]; blocks: BusinessBlock[]; progress?: { id: string; text: string }[] }
export interface ChatMessage { role: 'user' | 'assistant'; content: string; created_at: string; metadata: StructuredReply | null; steps?: AgentStep[]; presentation?: Presentation; stream_state?: 'generating' | 'completed' | 'failed'; status_text?: string }
export interface SessionSummary { id: string; title: string; created_at: string; updated_at: string; preview?: string; message_count?: number }
export interface SessionDetail extends SessionSummary { messages: ChatMessage[] }
export interface ChatResult { session_id: string; reply: StructuredReply; user_message: ChatMessage; assistant_message: ChatMessage }
export interface StreamEvent { type: string; request_id?: string; text?: string; block?: BusinessBlock; result?: ChatResult; code?: string; message?: string; [key: string]: any }
export class StreamUnavailableError extends Error {}
export class SSEParser {
  private buffer = ''
  constructor(private onEvent: (value: StreamEvent) => void) {}
  feed(text: string, final = false) {
    this.buffer = (this.buffer + text).replace(/\r\n/g, '\n')
    let boundary: number
    while ((boundary = this.buffer.indexOf('\n\n')) >= 0) {
      const frame = this.buffer.slice(0, boundary)
      this.buffer = this.buffer.slice(boundary + 2)
      this.dispatch(frame)
    }
    if (final && this.buffer.trim()) { this.dispatch(this.buffer); this.buffer = '' }
  }
  private dispatch(frame: string) {
    const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).replace(/^ /, '')).join('\n')
    if (data) this.onEvent(JSON.parse(data))
  }
}
const http = axios.create({ baseURL: '/api', timeout: 300_000, withCredentials: true, headers: { 'X-Portal-Request': '1' } })
http.interceptors.response.use(value => value, error => {
  if (error.response?.status === 401) window.dispatchEvent(new Event('portal-expired'))
  return Promise.reject(error)
})
let debugToken = ''

export const chatApi = {
  async health(): Promise<{ status: string; model_configured: boolean; agent_mode: string }> { return (await http.get('/health')).data },
  async list(): Promise<SessionSummary[]> { return (await http.get('/sessions')).data.sessions },
  async create(): Promise<SessionDetail> { return (await http.post('/sessions')).data },
  async detail(id: string): Promise<SessionDetail> { return (await http.get('/sessions/' + id)).data },
  async send(id: string, message: string): Promise<ChatResult> { return (await http.post('/sessions/' + id + '/chat', { message })).data },
  async reset(id: string): Promise<SessionDetail> { return (await http.post('/sessions/' + id + '/reset')).data },
  async remove(id: string): Promise<void> { await http.delete('/sessions/' + id) },
  async debug(requestId: string): Promise<any> {
    if (!debugToken) debugToken = (await http.post('/debug/access', {}, { headers: { 'X-Debug-Request': '1' } })).data.token
    try { return (await http.get('/debug/traces/' + requestId, { headers: { 'X-Debug-Token': debugToken } })).data }
    catch (error) { if (axios.isAxiosError(error) && error.response?.status === 403) debugToken = ''; throw error }
  },
  async sendStream(id: string, message: string, onEvent: (event: StreamEvent) => void): Promise<ChatResult> {
    const response = await fetch('/api/sessions/' + id + '/chat/stream', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Portal-Request': '1' }, body: JSON.stringify({ message }), signal: AbortSignal.timeout(300_000) })
    if (!response.ok) {
      if (response.status === 401) window.dispatchEvent(new Event('portal-expired'))
      if ([405, 501].includes(response.status)) throw new StreamUnavailableError()
      const data = await response.json().catch(() => ({}))
      throw new Error(typeof data.detail === 'string' ? data.detail : '本次请求未完成，请稍后重试。')
    }
    if (!response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('服务未返回流式响应，请重新连接。')
    const reader = response.body?.getReader()
    if (!reader) throw new Error('连接中断，请重新加载。')
    const decoder = new TextDecoder()
    let result: ChatResult | undefined
    const pending: StreamEvent[] = []
    let firstTextPainted = false
    const parser = new SSEParser(event => pending.push(event))
    const dispatch = (event: StreamEvent) => {
      if (event.type === 'error') throw new Error('本次回复未完成，已保留收到的内容。请重新加载会话确认记录。')
      if (event.type === 'done') result = event.result
      onEvent(event)
    }
    const flush = async () => {
      for (const event of pending.splice(0)) {
        dispatch(event)
        if (!firstTextPainted && ['delta', 'response_delta'].includes(event.type) && event.text?.trim()) {
          firstTextPainted = true
          // Give the first real fragment a paint even if multiple SSE frames share a read.
          await new Promise<void>(resolve => {
            if (document.visibilityState !== 'visible') setTimeout(resolve, 0)
            else requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
          })
        }
      }
    }
    try {
      while (true) {
        const { done, value } = await reader.read()
        parser.feed(done ? decoder.decode() : decoder.decode(value, { stream: true }), done)
        await flush()
        if (result) return result
        if (done) break
      }
      throw new Error('连接已中断，本次回复可能尚未保存。请重新加载会话确认。')
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
  },
}

export function friendlyError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (!error.response) return '暂时无法连接客服服务，请稍后重新连接。'
    if (typeof error.response.data?.detail === 'string') return error.response.data.detail
    if (error.response.status === 404) return '记录不存在或服务已重启，请重新加载。'
    return '本次请求未完成，请稍后重试。'
  }
  if (error instanceof TypeError) return '连接中断，请重新加载会话确认记录。'
  return error instanceof Error ? error.message : '请求未完成，请稍后再试。'
}
