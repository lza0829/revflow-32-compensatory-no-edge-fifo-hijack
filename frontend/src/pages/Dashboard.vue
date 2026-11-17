<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson } from '../api'

const data = ref(null)
const err = ref('')

onMounted(async () => {
  try {
    data.value = await getJson('/api/dashboard')
  } catch (e) {
    err.value = String(e.message || e)
  }
})

function yuan(cents) {
  return (Number(cents || 0) / 100).toFixed(2)
}

const maxCount = computed(() => {
  if (!data.value) return 1
  return Math.max(data.value.正向笔数, data.value.冲正笔数, data.value.待归并, 1)
})
</script>

<template>
  <div>
    <p v-if="err" class="msg">{{ err }}</p>
    <template v-if="data">
      <section class="hero-balance">
        <div>
          <p class="label">账户余额合计</p>
          <p class="amount">¥ {{ yuan(data.余额合计分) }}</p>
          <p class="hint">
            待归并 {{ data.待归并 }} 笔
            · 补正向 {{ data.补正向笔数 }} 笔 / 对消边 {{ data.对消边数 }} 条
            <template v-if="data.最近归并">
              · 最近归并 #{{ data.最近归并.id }}（隔离 {{ data.最近归并.quarantine_count }}）
            </template>
          </p>
        </div>
        <div class="meter">
          <div class="meter-row">
            <span class="n">正向</span>
            <div class="bar"><span :style="{ width: (data.正向笔数 / maxCount * 100) + '%' }"></span></div>
            <span class="v">{{ data.正向笔数 }}</span>
          </div>
          <div class="meter-row">
            <span class="n">冲正</span>
            <div class="bar"><span :style="{ width: (data.冲正笔数 / maxCount * 100) + '%' }"></span></div>
            <span class="v">{{ data.冲正笔数 }}</span>
          </div>
          <div class="meter-row">
            <span class="n">未平</span>
            <div class="bar"><span :style="{ width: (data.未平隔离 / maxCount * 100) + '%' }"></span></div>
            <span class="v">{{ data.未平隔离 }}</span>
          </div>
        </div>
      </section>

      <h2 class="section-title">账户余额</h2>
      <div class="panel">
        <table>
          <thead><tr><th>编码</th><th>名称</th><th>余额（元）</th></tr></thead>
          <tbody>
            <tr v-for="a in data.账户" :key="a.id">
              <td>{{ a.code }}</td>
              <td>{{ a.name }}</td>
              <td>{{ yuan(a.balance_cents) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
