<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { Headset, Search, Send, Check, UserRound, ShoppingBag, Menu, X } from 'lucide-vue-next'
import AccountMenu from './AccountMenu.vue'
import MarkdownMessage from '../components/MarkdownMessage.vue'
import BusinessResults from '../components/BusinessResults.vue'
import { formatDateTime } from '../utils/datetime'
import { portalApi, portalError, type Channel, type Customer } from './api'
import { usePortalStore } from './store'
const portal = usePortalStore()
const customers = ref<Customer[]>([]), selected = ref(''), channel = ref<Channel | null>(null)
const serviceFilter = ref('')
const displayedMessages = computed(() => channel.value?.messages.filter(message => !serviceFilter.value || message.service_id === serviceFilter.value) ?? [])
const draft = ref(''), search = ref(''), filter = ref('all'), error = ref('')
const busy = ref(false), loading = ref(true), sidebarOpen = ref(false)
const conversation = ref<HTMLElement | null>(null)
const drafts = new Map<string, string>()
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false, generation = 0
const current = computed(() => customers.value.find(item => item.user_id === selected.value))
const visible = computed(() => customers.value.filter(item => (filter.value === 'all' || item.state === filter.value) && (item.name + item.username + item.preview + (item.service_title || '')).toLowerCase().includes(search.value.toLowerCase())))
const waiting = computed(() => customers.value.filter(item => item.state === 'waiting').length)
const active = computed(() => customers.value.filter(item => item.state === 'active').length)
const mine = computed(() => channel.value?.state === 'active' && channel.value.staff_id === portal.user?.id && (!serviceFilter.value || serviceFilter.value === channel.value.service_id))
const canSend = computed(() => mine.value && !busy.value && Boolean(draft.value.trim()))
function status(state?: string) { return state === 'waiting' ? '待接入' : state === 'active' ? '服务中' : '已结束' }
async function loadHistory() {
  const id = selected.value, version = ++generation
  if (!id) { channel.value = null; return }
  const data = await portalApi.history(id)
  if (disposed || id !== selected.value || version !== generation) return
  const box = conversation.value
  const bottom = !box || box.scrollHeight - box.scrollTop - box.clientHeight < 100
  const previous = channel.value?.messages.at(-1)?.id
  channel.value = data
  if (bottom && previous !== data.messages.at(-1)?.id) { await nextTick(); if (box) box.scrollTop = box.scrollHeight }
}
async function refresh() {
  try {
    const value = await portalApi.customers()
    if (disposed) return
    customers.value = value
    if (!selected.value && value.length) selected.value = value[0].user_id
    if (selected.value && !value.some(item => item.user_id === selected.value)) { selected.value = ''; channel.value = null }
    await loadHistory()
    error.value = ''
  } catch (e) { if (!disposed) error.value = portalError(e) }
  finally { loading.value = false }
}
async function poll() { await refresh(); if (!disposed) timer = setTimeout(poll, 2000) }
onMounted(() => void poll())
onUnmounted(() => { disposed = true; generation++; clearTimeout(timer) })
async function select(item: Customer) {
  if (busy.value) return
  drafts.set(selected.value, draft.value)
  serviceFilter.value = ''; selected.value = item.user_id; draft.value = drafts.get(selected.value) || ''; channel.value = null; sidebarOpen.value = false
  try { await loadHistory() } catch (e) { error.value = portalError(e) }
}
async function action(kind: 'claim' | 'reply' | 'close') {
  const id = selected.value, revision = channel.value?.revision
  if (!id || !revision || busy.value || (kind === 'reply' && !canSend.value)) return
  if (kind === 'close') {
    try { await ElMessageBox.confirm('结束后用户将恢复自动客服咨询，服务记录会保留。', '结束本次人工服务', { confirmButtonText: '结束服务', cancelButtonText: '继续接待', closeOnClickModal: false }) }
    catch { return }
  }
  busy.value = true; error.value = ''
  try {
    await portalApi.act(id, kind, revision, kind === 'reply' ? draft.value.trim() : undefined)
    if (kind === 'reply') { draft.value = ''; drafts.delete(id) }
    await refresh()
  } catch (e) { error.value = portalError(e); try { await loadHistory() } catch { /* keep original action error */ } }
  finally { busy.value = false }
}
function onKey(event: KeyboardEvent) { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void action('reply') } }
</script>
<template>
  <div class="shell support-shell" @keydown.esc="sidebarOpen = false">
    <button v-if="sidebarOpen" class="mobile-overlay" aria-label="关闭用户列表" @click="sidebarOpen = false"></button>
    <aside class="sidebar support-sidebar" :class="{ 'sidebar-open': sidebarOpen }" aria-label="客服用户列表">
      <div class="brand"><span class="brand-mark"><ShoppingBag :size="21" /></span><div class="brand-name"><strong>小夕客服</strong><span>人工服务工作台</span></div><button class="icon-button mobile-close" aria-label="关闭用户列表" @click="sidebarOpen = false"><X :size="18" /></button></div>
      <div class="support-counters"><div><strong>{{ waiting }}</strong><span>等待接入</span></div><div><strong>{{ active }}</strong><span>正在服务</span></div></div>
      <div class="sidebar-content"><label class="search"><Search :size="15" /><input v-model="search" aria-label="搜索用户" placeholder="搜索用户或消息" /></label><div class="support-filters"><button v-for="item in [{id:'all',name:'全部'},{id:'waiting',name:'待接入'},{id:'active',name:'服务中'},{id:'bot',name:'已结束'}]" :key="item.id" :class="{selected: filter === item.id}" @click="filter = item.id">{{ item.name }}</button></div>
        <nav class="session-list" aria-label="用户服务会话"><button v-for="item in visible" :key="item.user_id" class="support-customer" :class="{selected: selected === item.user_id}" :disabled="busy" @click="select(item)"><span class="account-avatar">{{ item.name.slice(0,1) }}</span><span class="support-customer-copy"><span><strong>{{ item.name }}</strong><small :class="'service-state-' + item.state">{{ status(item.state) }}</small></span><span class="session-preview">{{ item.preview || '用户申请了人工服务' }}</span><time :title="formatDateTime(item.updated_at)">{{ formatDateTime(item.updated_at) }}</time></span></button><p v-if="!visible.length" class="empty-list">{{ search ? '没有找到相关用户' : '暂无服务会话' }}</p></nav>
      </div><div class="sidebar-footer"><Headset :size="18" /><div><strong>按用户接待，连续沟通</strong><span>一个用户，一个服务会话</span></div></div>
    </aside>
    <main class="main"><header class="topbar"><div class="topbar-left"><button class="icon-button menu-button" aria-label="打开用户列表" @click="sidebarOpen = true"><Menu :size="20" /></button><div class="service-heading"><h1>{{ current?.name || '人工客服工作台' }}</h1><p v-if="current">@{{ current.username }}<span v-if="channel?.session_count"> · 已汇总 {{ channel.session_count }} 个咨询会话</span></p></div></div><div class="header-actions"><span v-if="channel" class="support-header-status" :class="'service-state-' + channel.state">{{ status(channel.state) }}</span><AccountMenu :disabled="busy" /></div></header>
      <p v-if="error" class="support-error" role="alert">{{ error }}<button :disabled="busy" @click="refresh">重新加载</button></p>
      <div v-if="loading" class="center-state" role="status">正在加载服务会话…</div>
      <div v-else-if="!current" class="support-empty"><span class="auth-service-mark"><Headset :size="34" :stroke-width="1.5" /></span><h2>等待下一次咨询</h2><p>用户转人工后，会出现在左侧列表。<br />选择用户即可查看其全部咨询历史。</p></div>
      <template v-else>
        <div class="support-context"><span>已汇总此用户的咨询历史，自动客服与人工回复按时间排列。</span><button v-if="channel?.state === 'waiting'" class="portal-primary" :disabled="busy" @click="action('claim')"><Headset :size="14" />接入服务</button><button v-else-if="mine" class="portal-secondary" :disabled="busy" @click="action('close')"><Check :size="14" />结束服务</button></div>
        <label v-if="channel?.services?.length" class="service-picker">人工服务记录<select v-model="serviceFilter" aria-label="选择人工服务记录"><option value="">全部咨询历史</option><option v-for="(service, index) in channel.services" :key="service.id" :value="service.id">第 {{ index + 1 }} 次人工服务 · {{ service.ended_at ? '已结束' : '进行中' }}</option></select></label>
        <div ref="conversation" class="support-history" aria-label="用户全部咨询历史"><article v-for="message in displayedMessages" :key="message.id" :class="['support-message', 'kind-' + message.kind]">
          <p v-if="message.kind === 'system'" class="human-system">{{ message.content }}<time :title="formatDateTime(message.created_at)">{{ formatDateTime(message.created_at) }}</time></p>
          <template v-else><span class="message-avatar"><UserRound v-if="message.kind === 'user'" :size="16" /><Headset v-else :size="16" /></span><div class="support-message-body"><div class="message-heading"><span>{{ message.kind === 'user' ? current.name : message.author_name }}</span><time :title="formatDateTime(message.created_at)">{{ formatDateTime(message.created_at) }}</time></div><BusinessResults v-if="message.presentation?.blocks?.length" :blocks="message.presentation.blocks" /><MarkdownMessage :content="message.content" /></div></template>
        </article><p v-if="!channel?.messages.length" class="empty-list">尚无消息，接入后即可开始服务。</p></div>
        <div class="composer-wrap support-composer"><div v-if="!mine" class="support-readonly">{{ serviceFilter && serviceFilter !== channel?.service_id ? '正在查看历史服务，选择当前服务后可回复用户。' : channel?.state === 'waiting' ? '接入服务后，即可回复用户。' : '本次人工服务已结束，记录已保留。' }}</div><template v-else><div class="composer"><textarea v-model="draft" rows="2" maxlength="4000" aria-label="人工客服回复" placeholder="回复用户…" :disabled="busy" @keydown="onKey"></textarea><div class="composer-bottom"><span>以 {{ portal.user?.name }} 的身份回复</span><button class="send" :disabled="!canSend" @click="action('reply')"><Send :size="15" />发送</button></div></div><div class="composer-hint"><span>Enter 发送，Shift + Enter 换行</span><span>人工服务中</span></div></template></div>
      </template>
    </main>
  </div>
</template>
