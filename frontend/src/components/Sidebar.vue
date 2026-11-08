<script setup>
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import { auth } from '../lib/auth.js'

const props = defineProps({ items: Array, activeId: String, collapsed: Boolean, loading: Boolean })
const emit = defineEmits(['new', 'open', 'rename', 'remove', 'logout', 'toggle', 'profile'])
const editing = ref(null)
const draft = ref('')
const menuFor = ref(null)

const STATUS = { running: ['运行中', 'brand'], waiting_input: ['待回复', 'warn'], paused: ['已暂停', ''], failed: ['出错', 'err'],
  completed: ['已完成', 'ok'], closed: ['已关闭', ''], idle: ['未开始', ''] }

function group(d) {
  const t = new Date(d).getTime()
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (t >= today) return '今天'
  if (t >= today - 86400000) return '昨天'
  if (t >= today - 7 * 86400000) return '7 天内'
  if (t >= today - 30 * 86400000) return '30 天内'
  return '更早'
}
const groups = computed(() => {
  const out = []
  for (const c of props.items || []) {
    const g = group(c.updated_at)
    let last = out[out.length - 1]
    if (!last || last.name !== g) { last = { name: g, items: [] }; out.push(last) }
    last.items.push(c)
  }
  return out
})

function startEdit(c) { editing.value = c.id; draft.value = c.title; menuFor.value = null }
function commit(c) {
  const t = draft.value.trim()
  editing.value = null
  if (t && t !== c.title) emit('rename', c, t)
}
function remove(c) {
  menuFor.value = null
  if (confirm(`确定删除「${c.title}」？删除后不可恢复。`)) emit('remove', c)
}
</script>

<template>
  <nav class="side" :class="{ collapsed }" aria-label="历史对话">
    <div class="top">
      <div class="brand" v-if="!collapsed"><span class="logo"><Icon name="doc" /></span><b>Contract Copilot</b></div>
      <button class="icon-btn" @click="emit('toggle')" :aria-label="collapsed ? '展开侧栏' : '收起侧栏'"><Icon name="panel" /></button>
    </div>
    <button class="new" @click="emit('new')" data-testid="new-task" :title="'新建任务'">
      <Icon name="plus" /><span v-if="!collapsed">新建合同任务</span>
    </button>
    <div class="list" v-if="!collapsed">
      <div v-if="loading && !items?.length" class="muted pad">加载中…</div>
      <div v-else-if="!items?.length" class="muted pad">还没有任务，点击上方新建</div>
      <div v-for="g in groups" :key="g.name" class="group">
        <div class="gname">{{ g.name }}</div>
        <div v-for="c in g.items" :key="c.id" class="item" :class="{ active: c.id === activeId }" @click="editing !== c.id && emit('open', c)"
             role="button" tabindex="0" @keydown.enter="emit('open', c)" :data-testid="'conv-' + c.id">
          <input v-if="editing === c.id" v-model="draft" class="rename" maxlength="200" @click.stop @keydown.enter.prevent="commit(c)"
                 @keydown.esc="editing = null" @blur="commit(c)" v-focus />
          <template v-else>
            <span class="t">{{ c.title }}</span>
            <span v-if="STATUS[c.status] && c.status !== 'idle'" class="chip st" :class="STATUS[c.status][1]">
              <span v-if="c.status === 'running' || c.running" class="spinner" style="width:9px;height:9px;border-width:1.5px" />{{ STATUS[c.status][0] }}
            </span>
            <button class="icon-btn more" @click.stop="menuFor = menuFor === c.id ? null : c.id" aria-label="更多操作"><Icon name="menu" /></button>
            <div v-if="menuFor === c.id" class="menu" @click.stop>
              <button @click="startEdit(c)"><Icon name="edit" />重命名</button>
              <button class="danger" @click="remove(c)"><Icon name="trash" />删除</button>
            </div>
          </template>
        </div>
      </div>
    </div>
    <div class="me" v-if="!collapsed">
      <button class="me-btn" @click="emit('profile')" title="个人主页" data-testid="profile">
        <img v-if="auth.user.value?.avatar" :src="auth.user.value.avatar" class="av img" alt="" />
        <span v-else class="av">{{ (auth.user.value?.display_name || auth.user.value?.username || '?').slice(0, 1).toUpperCase() }}</span>
        <span class="name">{{ auth.user.value?.display_name || auth.user.value?.username }}</span>
      </button>
      <button class="icon-btn" @click="emit('logout')" title="退出登录" aria-label="退出登录" data-testid="logout"><Icon name="logout" /></button>
    </div>
  </nav>
</template>

<script>
export default { directives: { focus: { mounted: (el) => { el.focus(); el.select() } } } }
</script>

<style scoped>
.side { width: 264px; flex: none; min-width: 0; background: var(--side); border-right: 1px solid var(--line); display: flex; flex-direction: column; height: 100%; transition: width .18s; }
.side.collapsed { width: 60px; align-items: center; }
.top { display: flex; align-items: center; justify-content: space-between; padding: 12px 10px 8px 14px; width: 100%; }
.side.collapsed .top { justify-content: center; padding: 12px 0 8px; }
.brand { display: flex; align-items: center; gap: 8px; font-size: 15px; }
.logo { width: 26px; height: 26px; border-radius: 8px; background: var(--brand); color: #fff; display: grid; place-items: center; }
.logo :deep(svg) { width: 16px; height: 16px; }
.new { margin: 4px 12px 10px; height: 38px; border-radius: 10px; border: 1px solid #dcdfe6; background: #fff; display: flex; align-items: center; justify-content: center; gap: 6px; color: var(--ink); font-weight: 500; }
.new:hover { border-color: var(--brand-2); color: var(--brand); }
.side.collapsed .new { width: 38px; margin: 4px 0 10px; }
.list { flex: 1; overflow: auto; padding: 0 8px; }
.pad { padding: 12px 8px; }
.gname { font-size: 12px; color: var(--muted); padding: 10px 8px 4px; }
.item { position: relative; display: flex; align-items: center; gap: 6px; height: 36px; padding: 0 4px 0 10px; border-radius: 8px; cursor: pointer; color: var(--ink); }
.item:hover { background: rgba(0, 0, 0, .045); }
.item.active { background: #e6e8f0; }
.item .t { flex: 1; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 13.5px; }
.st { flex: none; height: 18px; font-size: 11px; padding: 0 6px; }
.more { width: 26px; height: 26px; opacity: 0; }
.item:hover .more, .item.active .more { opacity: 1; }
.more :deep(svg) { width: 15px; height: 15px; }
.menu { position: absolute; right: 4px; top: 34px; z-index: 10; background: #fff; border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 8px 24px rgba(0, 0, 0, .1); padding: 4px; min-width: 120px; }
.menu button { display: flex; align-items: center; gap: 8px; width: 100%; border: 0; background: transparent; padding: 7px 10px; border-radius: 6px; font-size: 13px; color: var(--ink); }
.menu button:hover { background: var(--line-2); }
.menu .danger { color: var(--err); }
.menu :deep(svg) { width: 15px; height: 15px; }
.rename { flex: 1; height: 28px; border: 1px solid var(--brand-2); border-radius: 6px; padding: 0 8px; outline: none; }
.me { display: flex; align-items: center; gap: 10px; padding: 12px 12px 14px; border-top: 1px solid var(--line); }
.me-btn { flex: 1; min-width: 0; display: flex; align-items: center; gap: 10px; border: 0; background: transparent; padding: 4px 6px; margin: -4px -6px; border-radius: 10px; text-align: left; color: var(--ink); }
.me-btn:hover { background: rgba(0, 0, 0, .05); }
.av { width: 30px; height: 30px; border-radius: 50%; background: #c7d2fe; color: #3730a3; display: grid; place-items: center; font-weight: 600; flex: none; }
.av.img { object-fit: cover; }
.name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.muted { color: var(--muted); font-size: 12.5px; }
</style>
