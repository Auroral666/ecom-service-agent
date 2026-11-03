import axios from 'axios'
import type { ChatMessage } from '../api/chat'

export type Role = 'user' | 'support' | 'dev'
export interface User { id: string; username: string; name: string; role: Role }
export interface HumanMessage {
  id: string; user_id: string; session_id: string; kind: 'user' | 'staff' | 'system' | 'ai'
  author_name: string; content: string; created_at: string; session_title?: string
  presentation?: ChatMessage['presentation']
  service_id?: string; service_title?: string; service_session_id?: string
}
export interface HumanService { id: string; title: string; session_id: string; started_at: string; ended_at: string | null }
export interface Channel {
  user_id?: string; state: 'bot' | 'waiting' | 'active'; revision: number
  staff_id?: string; staff_name?: string; messages: HumanMessage[]; session_count?: number
  service_id?: string; service_title?: string; service_started_at?: string; services?: HumanService[]
}
export interface Customer extends Channel { user_id: string; name: string; username: string; preview: string; updated_at: string }
export const roleNames: Record<Role, string> = { user: '用户', support: '客服', dev: '开发人员' }
const http = axios.create({ baseURL: '/api', timeout: 15000, withCredentials: true, headers: { 'X-Portal-Request': '1' } })
http.interceptors.response.use(value => value, error => {
  if (error.response?.status === 401 && !['/auth/login', '/auth/register', '/auth/me'].includes(error.config?.url)) window.dispatchEvent(new Event('portal-expired'))
  return Promise.reject(error)
})

export const portalApi = {
  async me(): Promise<User> { return (await http.get('/auth/me')).data.user },
  async enter(mode: 'login' | 'register', data: Record<string, string>): Promise<User> { return (await http.post('/auth/' + mode, data)).data.user },
  async logout() { await http.post('/auth/logout') },
  async channel(): Promise<Channel> { return (await http.get('/human/channel')).data },
  async transfer(session_id: string) { await http.post('/human/request', { session_id }) },
  async humanMessage(session_id: string, message: string): Promise<Channel> { return (await http.post('/human/messages', { session_id, message })).data },
  async customers(): Promise<Customer[]> { return (await http.get('/support/customers')).data.customers },
  async history(userId: string): Promise<Channel> { return (await http.get('/support/customers/' + userId)).data },
  async act(userId: string, action: 'claim' | 'reply' | 'close', revision: number, message?: string) { await http.post('/support/customers/' + userId + '/' + action, { revision, message }) },
}

export function portalError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (!error.response) return '无法连接服务，请检查后端是否启动。'
    const detail = error.response.data?.detail
    if (typeof detail === 'string') return detail
    if (error.response.status === 422) return '请检查账号、密码和表单内容。'
  }
  return '操作未完成，请稍后再试。'
}
