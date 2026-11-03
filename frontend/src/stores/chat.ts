import { defineStore } from 'pinia'
import { reactive, ref } from 'vue'
import type { StreamEvent, ToolEvent } from '../api/chat'
import {
  chatApi, friendlyError, StreamUnavailableError,
  type ChatMessage, type ChatResult, type SessionDetail, type SessionSummary,
} from '../api/chat'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<SessionSummary[]>([])
  const active = ref<SessionDetail | null>(null)
  const loading = ref(true)
  const sending = ref(false)
  const error = ref('')
  const modelReady = ref(false)
  const serviceReady = ref(false)
  const agentMode = ref('')

  function clearAccount() {
    sessions.value = []; active.value = null; loading.value = true
    sending.value = false; error.value = ''; modelReady.value = false; serviceReady.value = false
  }

  async function refreshSessions() {
    sessions.value = await chatApi.list()
  }

  async function initialize() {
    if (sending.value) return
    loading.value = true
    error.value = ''
    serviceReady.value = false
    try {
      const health = await chatApi.health()
      serviceReady.value = health.status === 'ok'
      modelReady.value = health.model_configured
      agentMode.value = health.agent_mode
      await refreshSessions()
      const current = sessions.value.find(session => session.id === active.value?.id)
      if (sessions.value.length) {
        active.value = await chatApi.detail(current?.id ?? sessions.value[0].id)
      } else {
        active.value = await chatApi.create()
        await refreshSessions()
      }
    } catch (e) {
      error.value = friendlyError(e)
    } finally {
      loading.value = false
    }
  }

  async function updateSession(action: () => Promise<void>) {
    if (sending.value || loading.value) return
    loading.value = true
    error.value = ''
    try {
      await action()
    } catch (e) {
      error.value = friendlyError(e)
    } finally {
      loading.value = false
    }
  }

  async function selectSession(id: string) {
    if (active.value?.id === id) return
    await updateSession(async () => {
      active.value = await chatApi.detail(id)
    })
  }

  async function createSession() {
    await updateSession(async () => {
      active.value = await chatApi.create()
      await refreshSessions()
    })
  }

  async function sendMessage(value: string): Promise<boolean> {
    const message = value.trim()
    if (!message || !active.value || sending.value || loading.value) return false
    const target = active.value
    sending.value = true
    error.value = ''

    // Use reactive objects so streamed steps and the final reply update immediately.
    const optimisticUser = reactive<ChatMessage>({
      role: 'user', content: message, created_at: new Date().toISOString(), metadata: null,
    })
    const optimisticAssistant = reactive<ChatMessage>({
      role: 'assistant', content: '', created_at: new Date().toISOString(), metadata: null, steps: [],
      stream_state: 'generating', status_text: '正在处理您的咨询…', presentation: { tools: [], blocks: [] },
    })
    const optimisticIndex = target.messages.length
    target.messages.push(optimisticUser, optimisticAssistant)

    try {
      let result: ChatResult
      try {
        result = await chatApi.sendStream(target.id, message, (event: StreamEvent) => {
          const view = optimisticAssistant.presentation!
          if (event.type === 'start') view.request_id = event.request_id
          if (event.type === 'status') optimisticAssistant.status_text = event.text
          if (event.type === 'delta' || event.type === 'response_delta') optimisticAssistant.content += event.text || ''
          if (event.type === 'response_end') {
            if (event.kind === 'activity') {
              view.progress ??= []
              view.progress.push({ id: event.id, text: event.text || '' })
              optimisticAssistant.content = ''
            } else optimisticAssistant.status_text = '正文已生成，正在保存…'
          }
          if (event.type === 'tool') {
            const index = view.tools.findIndex(tool => tool.id === event.id)
            if (index < 0) view.tools.push(event as unknown as ToolEvent)
            else view.tools[index] = event as unknown as ToolEvent
          }
          if (event.type === 'business' && event.block) view.blocks.push(event.block)
        })
      } catch (streamError) {
        // Only fall back when the stream endpoint is unsupported. Retrying a
        // failed, already-started request could perform a business action twice.
        if (!(streamError instanceof StreamUnavailableError)) throw streamError
        result = await chatApi.send(target.id, message)
      }

      optimisticAssistant.content = result.assistant_message.content
      optimisticAssistant.metadata = result.assistant_message.metadata
      optimisticAssistant.stream_state = 'completed'
      if (result.assistant_message.presentation) optimisticAssistant.presentation = result.assistant_message.presentation
      optimisticAssistant.steps = result.assistant_message.steps?.length
        ? result.assistant_message.steps : optimisticAssistant.steps
      optimisticAssistant.created_at = result.assistant_message.created_at
      optimisticUser.created_at = result.user_message.created_at
      target.title = target.title === '新会话' ? message.slice(0, 26) : target.title
      target.updated_at = result.assistant_message.created_at
      try {
        await refreshSessions()
      } catch (e) {
        error.value = friendlyError(e)
      }
      return true
    } catch (e) {
      if (optimisticAssistant.content || optimisticAssistant.presentation?.tools.length) optimisticAssistant.stream_state = 'failed'
      else target.messages.splice(optimisticIndex, 2)
      error.value = friendlyError(e)
      return false
    } finally {
      sending.value = false
    }
  }

  async function resetSession() {
    const id = active.value?.id
    if (!id) return
    await updateSession(async () => {
      active.value = await chatApi.reset(id)
      await refreshSessions()
    })
  }

  async function removeSession(id: string) {
    await updateSession(async () => {
      await chatApi.remove(id)
      await refreshSessions()
      if (active.value?.id === id) {
        active.value = sessions.value.length
          ? await chatApi.detail(sessions.value[0].id) : await chatApi.create()
        await refreshSessions()
      }
    })
  }

  return {
    sessions, active, loading, sending, error, modelReady, serviceReady, agentMode,
    initialize, selectSession, createSession, sendMessage, resetSession, removeSession, clearAccount,
  }
})
