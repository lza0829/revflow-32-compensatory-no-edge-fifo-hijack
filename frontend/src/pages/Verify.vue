<script setup>
import { onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const rows = ref([])
const comp = ref(null)
const err = ref('')
const msg = ref('')
const busy = ref(false)

async function load() {
  rows.value = await getJson('/api/verify-reports')
  comp.value = await getJson('/api/compensatory-report')
}

onMounted(async () => {
  try {
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

async function runVerify() {
  busy.value = true
  err.value = ''
  msg.value = ''
  try {
    const r = await postJson('/api/verify-reports')
    msg.value = r.consistent
      ? `互证一致：流水净额 ${(r.ledger_net_cents / 100).toFixed(2)} = 余额合计`
      : `互证不一致：流水 ${(r.ledger_net_cents / 100).toFixed(2)} vs 余额 ${(r.balance_sum_cents / 100).toFixed(2)}`
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function yuan(cents) {
  return (Number(cents || 0) / 100).toFixed(2)
}
</script>

<template>
  <div>
    <h1>净额互证</h1>
    <p class="lead">核对「已归并流水净额」与「账户余额合计」是否一致（隔离项不计入净额）。</p>
    <div class="row-actions">
      <button class="btn" :disabled="busy" @click="runVerify">跑净额互证</button>
    </div>
    <p v-if="msg" :class="msg.includes('一致') && !msg.includes('不一致') ? 'ok' : 'msg'">{{ msg }}</p>
    <p v-if="err" class="msg">{{ err }}</p>

    <div v-if="comp" class="panel">
      <h2>补正向对消报告</h2>
      <p class="note-strip">
        与流水、对消边、敞口、余额同一快照读出：补正向 {{ comp.补正向笔数 }} 笔 ·
        对消边 {{ comp.对消边数 }} 条 · 对余额影响 {{ yuan(comp.补正向金额分) }} 元。
      </p>
      <div class="stats">
        <div class="stat">
          <div class="k">补正向笔数</div>
          <div class="v">{{ comp.补正向笔数 }}</div>
        </div>
        <div class="stat">
          <div class="k">对消边数</div>
          <div class="v">{{ comp.对消边数 }}</div>
        </div>
        <div class="stat">
          <div class="k">补正向金额（元）</div>
          <div class="v">{{ yuan(comp.补正向金额分) }}</div>
        </div>
        <div class="stat">
          <div class="k">对消分合计（元）</div>
          <div class="v">{{ yuan(comp.对消分合计) }}</div>
        </div>
        <div class="stat">
          <div class="k">未对消补额（元）</div>
          <div class="v">{{ yuan(comp.补正向未对消分) }}</div>
        </div>
        <div class="stat">
          <div class="k">流水 / 余额一致</div>
          <div class="v">
            <span class="tag" :class="comp.流水余额一致 ? 'on' : 'bad'">
              {{ comp.流水余额一致 ? '是' : '否' }}
            </span>
          </div>
        </div>
      </div>

      <table>
        <thead>
          <tr>
            <th>补正向键</th><th>冲正键</th><th>账户</th>
            <th>补额（元）</th><th>对消分（元）</th><th>引用冲正金额（元）</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in comp.对消边" :key="e.id">
            <td>{{ e.forward_entry_key }}</td>
            <td>{{ e.reverse_entry_key }}</td>
            <td>{{ e.account_code }}</td>
            <td>{{ yuan(e.forward_amount_cents) }}</td>
            <td>{{ yuan(e.offset_cents) }}</td>
            <td>{{ yuan(e.reverse_amount_cents) }}</td>
            <td>{{ e.created_at }}</td>
          </tr>
          <tr v-if="!comp.对消边.length"><td colspan="7">尚无补正向对消边</td></tr>
        </tbody>
      </table>

      <h2 style="margin-top: 22px">引用冲正敞口</h2>
      <table>
        <thead>
          <tr><th>冲正键</th><th>冲正金额（元）</th><th>已对消（元）</th><th>未对消额度（元）</th></tr>
        </thead>
        <tbody>
          <tr v-for="x in comp.引用冲正敞口" :key="x.reverse_entry_key">
            <td>{{ x.reverse_entry_key }}</td>
            <td>{{ yuan(x.amount_cents) }}</td>
            <td>{{ yuan(x.offset_cents) }}</td>
            <td>{{ yuan(x.未对消额度分) }}</td>
          </tr>
          <tr v-if="!comp.引用冲正敞口.length"><td colspan="4">尚无被引用冲正</td></tr>
        </tbody>
      </table>

      <h2 style="margin-top: 22px">对余额影响（按账户）</h2>
      <table>
        <thead>
          <tr><th>账户</th><th>补正向笔数</th><th>补正向金额（元）</th><th>当前余额（元）</th></tr>
        </thead>
        <tbody>
          <tr v-for="a in comp.账户影响" :key="a.id">
            <td>{{ a.code }} · {{ a.name }}</td>
            <td>{{ a.compensatory_count }}</td>
            <td>{{ yuan(a.compensatory_cents) }}</td>
            <td>{{ yuan(a.balance_cents) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>互证报告</h2>
      <table>
        <thead>
          <tr>
            <th>#</th><th>归并#</th><th>补正向</th><th>对消边</th><th>流水净额</th><th>余额合计</th><th>隔离数</th><th>一致</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.settle_run_id }}</td>
            <td>{{ r.detail?.补正向笔数 ?? '—' }}</td>
            <td>{{ r.detail?.对消边数 ?? '—' }}</td>
            <td>{{ yuan(r.ledger_net_cents) }}</td>
            <td>{{ yuan(r.balance_sum_cents) }}</td>
            <td>{{ r.quarantine_count }}</td>
            <td>
              <span class="tag" :class="r.consistent ? 'on' : 'bad'">
                {{ r.consistent ? '是' : '否' }}
              </span>
            </td>
            <td>{{ r.created_at }}</td>
          </tr>
          <tr v-if="!rows.length"><td colspan="9">尚无报告</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
