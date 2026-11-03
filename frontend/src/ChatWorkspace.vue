<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import {
  ArrowRight, ChevronRight, Headset, Menu, MessageCircle, MoreHorizontal,
  Package, Plus, RotateCcw, Search, Send, ShoppingBag, Trash2, Truck, X,
} from 'lucide-vue-next'
import MarkdownMessage from './components/MarkdownMessage.vue'
import ToolActivity from './components/ToolActivity.vue'
import BusinessResults from './components/BusinessResults.vue'
import DebugPanel from './components/DebugPanel.vue'
import AccountMenu from './portal/AccountMenu.vue'
import { usePortalStore } from './portal/store'
import { portalApi, portalError } from './portal/api'
import type { ChatMessage } from './api/chat'
import { presentation } from './api/presentation'
import { formatDateTime } from './utils/datetime'
import { useChatStore } from './stores/chat'

const store = useChatStore()
const portal = usePortalStore()
const isDev = computed(() => portal.user?.role === 'dev')
const humanSending = ref(false), channelReady = ref(false)
const inHumanService = computed(() => portal.channel.state !== 'bot')
let channelTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false
async function syncChannel() {
  try { await portal.refreshChannel(); channelReady.value = true }
  catch (e) { channelReady.value = false; store.error = portalError(e) }
  if (!disposed) channelTimer = setTimeout(syncChannel, 1500)
}
async function transferHuman() {
  if (busy.value || !store.active || inHumanService.value) return
  humanSending.value = true
  try { await portalApi.transfer(store.active.id); await portal.refreshChannel(); store.error = '' }
  catch (e) { store.error = portalError(e) }
  finally { humanSending.value = false }
}
const draft = ref('')
const search = ref('')
const sidebarOpen = ref(false)
const debugMode = ref(false)
const debugRequestId = ref<string>()
const stickToBottom = ref(true)
const latestRequestId = computed(() => [...activeMessages.value].reverse().find(message => message.presentation?.request_id)?.presentation?.request_id)
function onConversationScroll() {
  const element = conversation.value
  if (element) stickToBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 100
}
function inspectRequest(id?: string) { if (!isDev.value) return; debugRequestId.value = id; debugMode.value = true }
const conversation = ref<HTMLElement | null>(null)
const input = ref<HTMLTextAreaElement | null>(null)
const actionMenu = ref<HTMLDetailsElement | null>(null)
const menuButton = ref<HTMLButtonElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
type DisplayedMessage = ChatMessage & { human_kind?: string; author_name?: string }
const activeMessages = computed<DisplayedMessage[]>(() => {
  const messages: DisplayedMessage[] = [...(store.active?.messages ?? [])]
  const human: DisplayedMessage[] = portal.channel.messages.filter(message =>
    message.session_id === store.active?.id || message.service_session_id === store.active?.id ||
    (inHumanService.value && message.service_id === portal.channel.service_id),
  ).map(message => ({
    role: message.kind === 'user' ? 'user' as const : 'assistant' as const,
    content: message.content, created_at: message.created_at, metadata: null,
    human_kind: message.kind, author_name: message.author_name,
  }))
  return [...messages, ...human].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
})
const sessions = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return store.sessions.filter(session => session.title.toLocaleLowerCase().includes(query))
})
const busy = computed(() => store.sending || store.loading || humanSending.value || !channelReady.value)
const canSend = computed(() => Boolean(
  draft.value.trim() && store.active && !busy.value && store.serviceReady && (inHumanService.value || store.modelReady),
))
const statusText = computed(() => {
  if (store.loading) return '正在连接'
  if (!store.serviceReady || !store.modelReady) return '暂不可用'
  return '服务正常'
})
const shortcuts = [
  { title: '查询订单', description: '查看订单与支付状态', icon: Package, prompt: '我想查询订单状态。' },
  { title: '物流查询', description: '了解配送进度', icon: Truck, prompt: '我想查询订单的物流进度。' },
  { title: '商品咨询', description: '了解商品规格与选购建议', icon: ShoppingBag, prompt: '我想咨询商品信息和选购建议。' },
  { title: '售后服务', description: '咨询退换货与退款', icon: RotateCcw, prompt: '我想了解退换货和退款流程。' },
]

function formatTime(value: string) { return formatDateTime(value) }
function formatSessionTime(value: string) { return formatDateTime(value) }

function closeActions() {
  if (actionMenu.value) actionMenu.value.open = false
}

function onOutsideClick(event: MouseEvent) {
  if (event.target instanceof Node && !actionMenu.value?.contains(event.target)) closeActions()
}

onMounted(() => {
  void store.initialize()
  void syncChannel()
  document.addEventListener('click', onOutsideClick)
})
onUnmounted(() => { disposed = true; clearTimeout(channelTimer); document.removeEventListener('click', onOutsideClick) })

watch(
  () => [
    store.active?.id, activeMessages.value.length, store.sending, store.loading,
    activeMessages.value.at(-1)?.content, activeMessages.value.at(-1)?.presentation?.blocks.length, activeMessages.value.at(-1)?.presentation?.tools.map(tool => tool.state).join(','), debugMode.value,
  ],
  async () => {
    await nextTick()
    if (conversation.value && stickToBottom.value) conversation.value.scrollTop = conversation.value.scrollHeight
  },
)
watch(draft, async () => {
  await nextTick()
  if (!input.value) return
  input.value.style.height = 'auto'
  input.value.style.height = Math.min(input.value.scrollHeight, 144) + 'px'
})

async function submit() {
  if (!canSend.value) return
  let success = false
  if (inHumanService.value) {
    humanSending.value = true
    try { portal.channel = await portalApi.humanMessage(store.active!.id, draft.value.trim()); store.error = ''; success = true }
    catch (e) { store.error = portalError(e); await portal.refreshChannel().catch(() => {}) }
    finally { humanSending.value = false }
  } else success = await store.sendMessage(draft.value)
  if (success) draft.value = ''
  await nextTick()
  input.value?.focus()
}

function onInputKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    void submit()
  }
}

async function usePrompt(prompt: string) {
  if (busy.value || !store.active) return
  draft.value = prompt
  await nextTick()
  input.value?.focus()
}

async function openSidebar() {
  sidebarOpen.value = true
  await nextTick()
  closeButton.value?.focus()
}

function closeSidebar() {
  sidebarOpen.value = false
  menuButton.value?.focus()
}

function onEscape() {
  closeActions()
  if (sidebarOpen.value) closeSidebar()
}

async function selectSession(id: string) {
  stickToBottom.value = true
  await store.selectSession(id)
  if (store.active?.id === id && sidebarOpen.value) closeSidebar()
}

async function createSession() {
  await store.createSession()
  search.value = ''
  if (sidebarOpen.value) closeSidebar()
}

async function confirmAction(action: 'reset' | 'remove', sessionId?: string) {
  closeActions()
  const id = sessionId || store.active?.id
  if (!id || busy.value) return
  const removing = action === 'remove'
  try {
    await ElMessageBox.confirm(
      removing ? '删除后无法恢复这段会话记录。' : '清空后无法恢复当前对话内容。',
      removing ? '删除会话' : '清空对话',
      {
        confirmButtonText: removing ? '删除' : '清空',
        cancelButtonText: '取消',
        type: 'warning',
        closeOnClickModal: false,
      },
    )
  } catch {
    return
  }
  if (removing) await store.removeSession(id)
  else await store.resetSession()
}
</script>

<template>
  <div class="shell" @keydown.esc="onEscape">
    <button v-if="sidebarOpen" class="mobile-overlay" aria-label="关闭会话列表" @click="closeSidebar"></button>

    <aside id="session-sidebar" class="sidebar" :class="{ 'sidebar-open': sidebarOpen }" aria-label="会话列表">
      <div class="brand">
        <span class="brand-mark"><ShoppingBag :size="21" :stroke-width="1.8" /></span>
        <div class="brand-name"><strong>小夕客服</strong><span>电商服务中心</span></div>
        <button ref="closeButton" class="icon-button mobile-close" aria-label="关闭会话列表" @click="closeSidebar"><X :size="20" /></button>
      </div>

      <div class="sidebar-content">
        <button class="new-chat" :disabled="busy" @click="createSession"><Plus :size="18" />新建会话</button>
        <div class="history-heading"><h2>历史会话</h2><span>{{ store.sessions.length }}</span></div>
        <label class="search">
          <Search :size="16" aria-hidden="true" />
          <input v-model="search" placeholder="搜索会话" aria-label="搜索会话" />
          <button v-if="search" class="clear-search" aria-label="清空搜索" @click="search = ''"><X :size="14" /></button>
        </label>
        <nav class="session-list" aria-label="历史会话">
          <div v-for="session in sessions" :key="session.id" class="session-row">
          <button class="session-item"
            :class="{ selected: store.active?.id === session.id }"
            :aria-current="store.active?.id === session.id ? 'true' : undefined"
            :disabled="busy" @click="selectSession(session.id)"
          >
            <MessageCircle :size="17" aria-hidden="true" />
            <span class="session-copy"><span class="session-title">{{ session.title === '新会话' ? '新的咨询' : session.title }}</span><span v-if="session.preview" class="session-preview">{{ session.preview }}</span><time :datetime="session.updated_at" :title="formatDateTime(session.updated_at)">{{ formatSessionTime(session.updated_at) }}</time></span>
            <ChevronRight v-if="store.active?.id === session.id" :size="14" class="session-chevron" aria-hidden="true" />
          </button>
          <details class="session-menu"><summary aria-label="管理会话"><MoreHorizontal :size="15" /></summary><button :disabled="busy" @click="confirmAction('remove', session.id)">删除会话</button></details>
          </div>
          <p v-if="!sessions.length" class="empty-list">{{ search.trim() ? '没有找到相关会话' : store.loading ? '正在加载会话…' : '暂无会话记录' }}</p>
        </nav>
      </div>

      <div class="sidebar-footer"><Headset :size="18" /><div><strong>购物有疑问，随时聊一聊</strong><span>商品 · 订单 · 物流 · 售后</span></div></div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="topbar-left">
          <button ref="menuButton" class="icon-button menu-button" aria-label="打开会话列表" aria-controls="session-sidebar" :aria-expanded="sidebarOpen" @click="openSidebar"><Menu :size="21" /></button>

          <div class="service-heading"><h1>{{ store.active?.title && store.active.title !== '新会话' ? store.active.title : '新的咨询' }}</h1></div>
        </div>
        <div class="header-actions">
          <span class="service-status" :class="{ unavailable: !store.serviceReady || !store.modelReady, connecting: store.loading }" role="status"><span class="status-dot"></span>{{ statusText === '服务正常' ? '已连接' : statusText }}</span>
          <details ref="actionMenu" class="actions-menu">
            <summary class="icon-button" aria-label="更多操作" title="更多操作"><MoreHorizontal :size="21" /></summary>
            <div class="actions-popover">
              <button :disabled="busy || !store.active || inHumanService" @click="confirmAction('reset')"><RotateCcw :size="15" />清空对话</button>
              <button class="danger-action" :disabled="busy || !store.active || inHumanService" @click="confirmAction('remove')"><Trash2 :size="15" />删除会话</button>
              <label v-if="isDev" class="debug-toggle"><input v-model="debugMode" type="checkbox" @change="closeActions" />显示调试信息</label>
            </div>
          </details>
          <AccountMenu :disabled="busy" />
        </div>
      </header>

      <div class="workspace-content" :class="{ 'with-debug': isDev && debugMode }">
      <section class="conversation-pane" aria-label="客服对话">
        <div class="human-bar"><div class="human-bar-copy"><Headset :size="16" /><span>{{ portal.channel.state === 'waiting' ? '等待人工客服接入' : portal.channel.state === 'active' ? (portal.channel.staff_name || '人工客服') + ' 正在为您服务' : '自动客服' }}</span><small>{{ inHumanService ? '所有咨询会话的后续消息均交由人工处理' : '需要进一步帮助，可转接人工客服' }}</small></div><button class="portal-secondary" :disabled="busy || !store.active || inHumanService" @click="transferHuman">{{ portal.channel.state === 'waiting' ? '等待接入' : portal.channel.state === 'active' ? '人工服务中' : '转人工' }}</button></div>
        <div ref="conversation" class="conversation-scroll" @scroll="onConversationScroll" :aria-busy="busy">
          <div v-if="store.loading" class="center-state" role="status"><span class="loading-dot"></span>正在加载会话…</div>
          <div v-else-if="!store.active" class="connection-state">
            <Headset :size="30" :stroke-width="1.5" />
            <h2>暂时无法连接客服</h2><p>请稍后重新连接，继续您的咨询。</p>
            <button class="secondary-button" @click="store.initialize()"><RotateCcw :size="15" />重新连接</button>
          </div>
          <div v-else-if="!activeMessages.length" class="welcome">
            <span class="welcome-label">欢迎来到小夕客服</span>
            <h2>您好，有什么可以帮您？</h2>
            <p>从选购到收货，为您解答每一个购物问题。</p>
            <div class="welcome-services">
              <button v-for="item in shortcuts" :key="item.title" class="service-card" @click="usePrompt(item.prompt)">
                <span class="shortcut-icon"><component :is="item.icon" :size="21" :stroke-width="1.7" /></span>
                <span class="service-card-copy"><strong>{{ item.title }}</strong><span>{{ item.description }}</span></span>
                <ArrowRight :size="16" class="card-arrow" aria-hidden="true" />
              </button>
            </div>
            <p class="welcome-note">也可以直接在下方输入问题。</p>
          </div>
          <div v-else class="message-stream" role="log" aria-label="聊天记录" aria-live="polite" aria-relevant="additions text">
            <div class="conversation-start"><span>本次咨询</span></div>
            <article v-for="(message, index) in activeMessages" :key="store.active.id + '-' + index" class="message" :class="message.role">
              <div class="message-avatar" aria-hidden="true"><Headset v-if="message.role === 'assistant'" :size="18" /><span v-else>我</span></div>
              <div class="message-content" :class="{ 'human-event-content': message.human_kind === 'system' }">
                <div class="message-heading"><span>{{ message.role === 'user' ? '我' : message.human_kind === 'staff' ? message.author_name : message.human_kind === 'system' ? '服务通知' : '小夕客服' }}</span><span v-if="message.human_kind === 'staff'" class="human-badge">人工客服</span><time :datetime="message.created_at" :title="formatDateTime(message.created_at)">{{ formatTime(message.created_at) }}</time></div>
                <div v-for="entry in presentation(message).progress || []" :key="entry.id" class="reply-pending"><MarkdownMessage :content="entry.text" /></div>
                <ToolActivity v-if="message.role === 'assistant'" :tools="presentation(message).tools" />
                <BusinessResults v-if="message.role === 'assistant'" :blocks="presentation(message).blocks" />
                <MarkdownMessage v-if="message.role === 'assistant' && message.content" :content="message.content" :business-kinds="presentation(message).blocks.map(block => block.kind)" />
                <div v-else-if="message.content" class="message-text">{{ message.content }}</div>
                <div v-if="store.sending && index === activeMessages.length - 1 && !message.content" class="reply-pending" role="status"><span class="loading-dot"></span>{{ message.status_text || '正在处理您的咨询…' }}</div>
                <span v-if="message.stream_state === 'generating' && message.content" class="stream-label" role="status">{{ message.status_text || '正在生成…' }}</span>
                <p v-if="message.stream_state === 'failed'" class="partial-warning">回复未完成，请重新加载会话确认保存结果。</p>
                <button v-if="isDev && message.presentation?.request_id" class="message-debug" @click="inspectRequest(message.presentation.request_id)">执行详情</button>
                <p v-if="message.metadata?.requires_human" class="human-note">此问题可能需要人工协助。</p>
              </div>
            </article>
          </div>
        </div>

        <div class="composer-wrap">
          <div v-if="store.error" class="error-banner" role="alert">
            <span>{{ store.error }}</span>
            <button :disabled="busy" @click="store.initialize()">重新加载</button>
            <button class="error-dismiss" aria-label="关闭错误提示" @click="store.error = ''"><X :size="16" /></button>
          </div>
          <div v-if="activeMessages.length && !inHumanService" class="quick-actions" aria-label="快捷咨询">
            <button v-for="item in shortcuts" :key="item.title" :disabled="busy || !store.active" @click="usePrompt(item.prompt)"><component :is="item.icon" :size="15" />{{ item.title }}</button>
          </div>
          <div class="composer" :class="{ disabled: busy || !store.active }">
            <textarea
              ref="input" v-model="draft" rows="1" maxlength="4000" :disabled="busy || !store.active"
              :placeholder="inHumanService ? '发送给人工客服…' : '请输入您的问题…'" aria-label="聊天输入" aria-describedby="composer-hint"
              @keydown="onInputKeydown"
            ></textarea>
            <div class="composer-bottom"><span>{{ inHumanService ? '消息将发送给人工客服' : '可提供订单编号，便于查询' }}</span><button class="send" :disabled="!canSend" aria-label="发送消息" @click="submit"><Send :size="15" /><span>{{ store.sending || humanSending ? '处理中' : '发送' }}</span></button></div>
          </div>
          <div id="composer-hint" class="composer-hint"><span>Enter 发送<span class="desktop-hint">，Shift + Enter 换行</span></span><span>{{ draft.length ? draft.length + ' / 4000' : '小夕客服 · 用心解答' }}</span></div>
        </div>
      </section>
      <DebugPanel v-if="isDev && debugMode" :request-id="debugRequestId || latestRequestId" @close="debugMode = false" />
      </div>
    </main>
  </div>
</template>
