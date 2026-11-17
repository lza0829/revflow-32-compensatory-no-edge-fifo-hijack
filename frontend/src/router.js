import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from './pages/Dashboard.vue'
import Accounts from './pages/Accounts.vue'
import ForwardEntries from './pages/ForwardEntries.vue'
import ReversalEntries from './pages/ReversalEntries.vue'
import SettleDesk from './pages/SettleDesk.vue'
import Quarantine from './pages/Quarantine.vue'
import Verify from './pages/Verify.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Dashboard },
    { path: '/accounts', component: Accounts },
    { path: '/forward', component: ForwardEntries },
    { path: '/reversal', component: ReversalEntries },
    { path: '/settle', component: SettleDesk },
    { path: '/quarantine', component: Quarantine },
    { path: '/verify', component: Verify },
  ],
})
