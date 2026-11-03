<script setup lang="ts">
import { ChevronDown, LogOut } from 'lucide-vue-next'
import { roleNames } from './api'
import { usePortalStore } from './store'
defineProps<{ disabled?: boolean }>()
const portal = usePortalStore()
</script>
<template>
  <details v-if="portal.user" class="account-menu">
    <summary :aria-label="'登录用户：' + portal.user.name"><span class="account-avatar" aria-hidden="true">{{ portal.user.name.slice(0, 1) }}</span><span class="account-name"><strong>{{ portal.user.name }}</strong><small>{{ roleNames[portal.user.role] }}</small></span><ChevronDown :size="13" /></summary>
    <div class="account-popover"><span>@{{ portal.user.username }}</span><button :disabled="disabled" @click="portal.logout()"><LogOut :size="14" />退出登录</button><p v-if="portal.error" role="alert">{{ portal.error }}</p></div>
  </details>
</template>
