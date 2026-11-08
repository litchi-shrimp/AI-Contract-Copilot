import { createRouter, createWebHashHistory } from 'vue-router'
import { auth } from './lib/auth.js'
import Login from './views/Login.vue'
import Workspace from './views/Workspace.vue'
import Profile from './views/Profile.vue'

// Hash 路由：GitHub Pages 等静态托管无需服务端 rewrite
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: Login, meta: { public: true } },
    { path: '/', component: Workspace },
    { path: '/c/:id', component: Workspace },
    { path: '/me', component: Profile },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  if (!to.meta.public && !auth.token.value) return { path: '/login', query: { next: to.fullPath } }
  if (to.path === '/login' && auth.token.value) return '/'
})
