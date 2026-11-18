<template>
  <section class="page" data-module="dim_model">
    <header class="page-head">
      <div>
        <h2>维度建模管理</h2>
        <p class="page-desc">维护维度模型的输入、执行约束与结果记录，为维度建模提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记维度模型</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出维度建模清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" :data-testid="`pit-check-${row.id}`" @click="openPitCheck(row)">核对汇总结果</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无维度建模数据，可先登记维度模型</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记维度模型</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="pitCheckPanel" class="modal-backdrop" data-testid="dws-pit-panel">
      <div class="modal">
        <h3>核对DWS时点汇总结果 · {{ pitCheckPanel.维度名称 }}（{{ pitCheckPanel.维度模型 }}）</h3>
        <p class="page-desc">{{ pitCheckPanel.message }}；规则与阈值顺着汇总层管理调用链查找。</p>
        <div v-for="item in pitCheckPanel.汇总结果" :key="item.汇总表" class="pit-check-block" data-testid="pit-check-block">
          <h4>{{ item.汇总表 }} · {{ item.表名称 }}</h4>
          <p class="page-desc">
            规则阈值：下界 {{ item.规则阈值.阈值下界 || '—' }} ｜ 上界 {{ item.规则阈值.阈值上界 || '—' }} ｜
            条件标准：{{ item.规则阈值.条件标准 || '无' }}
          </p>
          <table class="data-table">
            <thead>
              <tr>
                <th>汇总窗口</th><th>生效维度键</th><th>维度版本</th><th>生效区间</th>
                <th>聚合函数</th><th>度量值</th><th>明细条数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(line, index) in flattenWindows(item.时点汇总结果)" :key="`${item.汇总表}-${index}`" data-testid="pit-check-row">
                <td>{{ line.汇总窗口 }}</td>
                <td>{{ line.维度键 }}</td>
                <td>{{ line.维度版本 }}</td>
                <td>{{ line.生效区间 }}</td>
                <td>{{ line.聚合函数 }}({{ line.度量字段 }})</td>
                <td>{{ line.度量值 }}</td>
                <td>{{ line.明细条数 }}</td>
              </tr>
              <tr v-if="!flattenWindows(item.时点汇总结果).length">
                <td colspan="7" class="empty-state">该汇总表尚未生成时点汇总结果</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="!pitCheckPanel.汇总结果.length" class="empty-state">
          该维度模型未关联任何DWS时点汇总表
        </div>
        <div class="page-actions">
          <button class="btn" type="button" @click="pitCheckPanel = null">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条维度建模记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/dim_model'
const columns = ["模型编号", "维度名称", "自然键列", "代理键列", "缓慢变化策略", "生效日期", "失效日期", "刷新周期"]
const actions = ["提交校验", "标记生效", "停用模型"]
const statuses = ["草稿", "待校验", "已生效", "已停用"]
const metricNames = ["维度模型总数", "待校验模型", "已生效模型"]

const route = useRoute()
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const pitCheckPanel = ref<Record<string, any> | null>(null)
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'dim_model.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('维度建模动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '维度建模操作失败'
  }
}

function flattenWindows(windows: Record<string, any>): Record<string, any>[] {
  return Object.values(windows ?? {}).flatMap((item: any) => item.rows ?? [])
}

async function openPitCheck(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/dws_pit_results`)
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || payload.detail || '汇总结果读取失败')
    pitCheckPanel.value = payload
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '汇总结果读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('维度模型列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '维度建模列表读取失败'
  }
}

onMounted(async () => {
  await reload()
  // 从DWS时点汇总表列表跳转而来：自动打开对应维度模型的核对面板
  const focus = route.query.focus
  if (typeof focus === 'string' && focus) {
    const target = rows.value.find((row) => row.模型编号 === focus)
    if (target) {
      await openPitCheck(target)
    }
  }
})
</script>
