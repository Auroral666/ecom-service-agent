<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'
import AuthPage from './portal/AuthPage.vue'
import SupportDesk from './portal/SupportDesk.vue'
import ChatWorkspace from './ChatWorkspace.vue'
import { usePortalStore } from './portal/store'
import { useChatStore } from './stores/chat'
const portal = usePortalStore()
const chat = useChatStore()
function expired() { portal.clear(); portal.error = '登录已过期，请重新登录。' }
watch(() => portal.user?.id, () => {
  chat.clearAccount()
  const page = !portal.user ? '/login' : portal.user.role === 'support' ? '/support' : portal.user.role === 'dev' ? '/dev' : '/chat'
  history.replaceState({}, '', page)
}, { immediate: true })
onMounted(() => { window.addEventListener('portal-expired', expired); void portal.initialize() })
onUnmounted(() => window.removeEventListener('portal-expired', expired))
</script>
<template>
  <main v-if="portal.loading" class="portal-loading" role="status"><span class="loading-dot"></span>正在连接客服中心…</main>
  <AuthPage v-else-if="!portal.user" />
  <SupportDesk v-else-if="portal.user.role === 'support'" :key="portal.user.id" />
  <ChatWorkspace v-else :key="portal.user.id" />
</template>
