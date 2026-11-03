import { defineStore } from 'pinia'
import { ref } from 'vue'
import { portalApi, portalError, type Channel, type User } from './api'

export const usePortalStore = defineStore('portal', () => {
  const user = ref<User | null>(null)
  const loading = ref(true)
  const error = ref('')
  const channel = ref<Channel>({ state: 'bot', revision: 0, messages: [] })
  const accountEvents = new BroadcastChannel('ecom-account')
  accountEvents.onmessage = () => { clear(); void initialize() }
  function clear() { user.value = null; channel.value = { state: 'bot', revision: 0, messages: [] } }
  async function initialize() {
    loading.value = true
    try { user.value = await portalApi.me() }
    catch (e: any) { clear(); if (e.response?.status !== 401) error.value = portalError(e) }
    finally { loading.value = false }
  }
  async function enter(mode: 'login' | 'register', data: Record<string, string>) {
    error.value = ''
    try { user.value = await portalApi.enter(mode, data); accountEvents.postMessage('changed'); return true }
    catch (e) { error.value = portalError(e); return false }
  }
  async function logout() {
    try { await portalApi.logout(); clear(); accountEvents.postMessage('changed') }
    catch (e) { error.value = portalError(e) }
  }
  async function refreshChannel() {
    const id = user.value?.id
    if (!id || user.value?.role === 'support') return
    const value = await portalApi.channel()
    if (user.value?.id === id) channel.value = value
  }
  return { user, loading, error, channel, initialize, enter, logout, refreshChannel, clear }
})
