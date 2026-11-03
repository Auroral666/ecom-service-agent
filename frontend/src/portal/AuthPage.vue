<script setup lang="ts">
import { computed, ref } from 'vue'
import { ShoppingBag, ArrowRight, Headset, Code2, UserRound, Eye, EyeOff } from 'lucide-vue-next'
import type { Role } from './api'
import { usePortalStore } from './store'
const portal = usePortalStore()
const mode = ref<'login' | 'register'>('login')
const role = ref<Role>('user')
const username = ref(''), password = ref(''), name = ref(''), invitation = ref(''), confirmation = ref('')
const busy = ref(false), visible = ref(false), localError = ref('')
const roles = [{ id: 'user' as Role, label: '用户', icon: UserRound, description: '咨询商品、订单与售后问题' }, { id: 'support' as Role, label: '客服', icon: Headset, description: '接待用户，处理人工咨询' }, { id: 'dev' as Role, label: '开发人员', icon: Code2, description: '验证服务，查看执行调试' }]
const selected = computed(() => roles.find(item => item.id === role.value)!)
function changeMode() { mode.value = mode.value === 'login' ? 'register' : 'login'; portal.error = ''; localError.value = ''; password.value = ''; confirmation.value = '' }
async function submit() {
  localError.value = ''
  if (mode.value === 'register' && password.value !== confirmation.value) { localError.value = '两次输入的密码不一致。'; return }
  busy.value = true
  try { await portal.enter(mode.value, { username: username.value.trim(), password: password.value, name: name.value.trim(), role: role.value, invitation: invitation.value.trim() }) }
  finally { busy.value = false }
}
</script>
<template>
  <main class="auth-page">
    <header class="auth-brand"><span class="brand-mark"><ShoppingBag :size="21" /></span><strong>小夕客服</strong><span>电商服务中心</span></header>
    <div class="auth-layout">
      <section class="auth-intro"><span class="auth-service-mark"><Headset :size="34" :stroke-width="1.5" /></span><h1>每一次咨询，<br />都有回应。</h1><p>从商品选购到订单售后，<br />让沟通简单，让服务更近一步。</p><div class="auth-service-list"><span>商品咨询</span><span>订单物流</span><span>售后服务</span></div><div class="auth-handoff-note"><span class="status-dot"></span><span>自动客服随时解答，人工客服接续服务。</span></div></section>
      <section class="auth-card" aria-labelledby="auth-title">
        <div class="auth-card-heading"><h2 id="auth-title">{{ mode === 'login' ? '欢迎登录' : '注册账号' }}</h2><p>{{ selected.description }}</p></div>
        <div class="auth-roles" role="group" aria-label="登录身份"><button v-for="item in roles" :key="item.id" type="button" :class="{ selected: role === item.id }" :aria-pressed="role === item.id" :disabled="busy" @click="role = item.id; portal.error = ''"><component :is="item.icon" :size="16" />{{ item.label }}</button></div>
        <form @submit.prevent="submit">
          <label v-if="mode === 'register'">名称<input v-model="name" autocomplete="nickname" maxlength="30" required placeholder="请输入您的名称" /></label>
          <label>账号<input v-model="username" autocomplete="username" pattern="[A-Za-z0-9_]{3,32}" minlength="3" maxlength="32" required placeholder="3–32 位字母、数字或下划线" /></label>
          <label>密码<span class="password-field"><input v-model="password" :type="visible ? 'text' : 'password'" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" minlength="8" maxlength="128" required placeholder="至少 8 位字符" /><button type="button" :aria-label="visible ? '隐藏密码' : '显示密码'" @click="visible = !visible"><component :is="visible ? EyeOff : Eye" :size="16" /></button></span></label>
          <label v-if="mode === 'register'">确认密码<input v-model="confirmation" :type="visible ? 'text' : 'password'" autocomplete="new-password" minlength="8" maxlength="128" required placeholder="请再次输入密码" /></label>
          <label v-if="mode === 'register' && role !== 'user'">员工邀请码<input v-model="invitation" type="password" autocomplete="off" required maxlength="128" placeholder="请向项目负责人获取" /></label>
          <p v-if="localError || portal.error" class="auth-error" role="alert">{{ localError || portal.error }}</p>
          <button class="portal-primary auth-submit" :disabled="busy">{{ busy ? '正在处理…' : mode === 'login' ? '登录' : '注册并登录' }}<ArrowRight :size="16" /></button>
        </form>
        <p class="auth-switch">{{ mode === 'login' ? '还没有账号？' : '已经有账号？' }}<button :disabled="busy" @click="changeMode">{{ mode === 'login' ? '注册账号' : '返回登录' }}</button></p>
      </section>
    </div>
    <footer class="auth-footer">小夕客服 · 商品、订单与售后咨询</footer>
  </main>
</template>
