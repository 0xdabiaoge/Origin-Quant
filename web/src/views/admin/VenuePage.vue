<script setup lang="ts">
import { ref } from "vue";
import { v1 } from "@/api/client";
import type { VenueReadback } from "@/api/types";
import { fmtTime } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";

const result = ref<VenueReadback | null>(null);
const error = ref("");
const busy = ref(false);

async function observe() {
  busy.value = true;
  error.value = "";
  result.value = null;
  try {
    result.value = await v1.venueReadback();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "查询失败";
  } finally {
    busy.value = false;
  }
}

function observed(value: string): string {
  return ({ matching_fields: "字段匹配，待核实", observed: "有查询结果", partial: "结果可能不完整", unknown: "未知" } as Record<string, string>)[value] ?? "未知";
}
</script>

<template>
  <div>
    <PageHeader title="交易所状态" subtitle="手动获取当前 OKX 环境的只读观察结果。">
      <template #actions><button class="btn" :disabled="busy" @click="observe">{{ busy ? '查询中…' : '查询交易所状态' }}</button></template>
    </PageHeader>

    <div class="mb-4 rounded-lg border border-warn-500/30 bg-warn-500/5 px-4 py-3 text-xs leading-relaxed text-mute-300">
      查询按本地未结订单和持仓检查部分交易所数据，仅在 live 模式及 OKX 凭据可用时执行。结果不修改账本，也不代表已完成对账或仓位已受保护。本地移动止损不代表交易所止损已同步。
    </div>
    <p v-if="error" role="alert" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ error }}。无法确定交易所订单和保护状态。</p>
    <div v-if="!result && !busy && !error" class="card card-pad text-sm text-mute-400">尚未查询；点击上方按钮后才会请求交易所。未查询不代表账户无订单或有保护。</div>
    <div v-if="busy" class="card card-pad text-sm text-mute-400">正在查询，请稍候。查询结果可能受接口或时间限制。</div>

    <template v-if="result">
      <div class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-mute-400">
        <span>查询时间：{{ fmtTime(result.observed_ms) }}</span>
        <span>交易所环境：{{ result.venue_environment === 'demo' ? '模拟盘' : result.venue_environment === 'live' ? '实盘' : '未知' }}</span>
        <span class="text-warn-500">只读观察 · 未完成对账</span>
      </div>
      <div v-if="result.orders_truncated || result.positions_truncated || result.time_budget_exceeded" class="mb-3 rounded-lg border border-warn-500/30 bg-warn-500/5 px-4 py-3 text-xs text-warn-500">查询范围或时间受限，结果不完整；未列出的记录状态未知。</div>

      <section class="card mb-4">
        <div class="border-b border-ink-800 px-4 py-3"><h2 class="text-sm font-semibold">本地未结订单观察</h2><p class="mt-1 text-xs text-mute-400">字段匹配仅供参考，未核实成交明细；客户端订单 ID 可能复用。</p></div>
        <div v-if="!result.orders.length" class="p-4 text-sm text-mute-500">本次没有本地未结订单可查询；不代表交易所没有其他订单。</div>
        <div v-for="order in result.orders" :key="order.client_id" class="border-b border-ink-800 p-4 last:border-0">
          <div class="flex flex-wrap items-center gap-2 text-xs"><span class="tnum text-paper">{{ order.symbol }}</span><span class="chip bg-warn-500/10 text-warn-500">{{ observed(order.observation) }}</span><span class="text-mute-400">本地：{{ order.local_status }} · 交易所：{{ order.venue_state ?? '未知' }}</span></div>
          <p class="mt-2 break-all text-xs text-mute-400">客户端订单 ID：{{ order.client_id }} · 交易所已成交基础币数量：{{ order.venue_filled_base ?? '未知' }}</p>
          <p class="mt-2 text-xs text-mute-500">{{ order.note }}</p>
        </div>
      </section>

      <section class="card">
        <div class="border-b border-ink-800 px-4 py-3"><h2 class="text-sm font-semibold">持仓保护单观察</h2><p class="mt-1 text-xs text-mute-400">条件单归属、实际持仓和保护覆盖均未核实；不能根据此页认定有保护。</p></div>
        <div v-if="!result.protection.length" class="p-4 text-sm text-mute-500">本次没有本地持仓可查询；不代表交易所没有仓位。</div>
        <div v-for="position in result.protection" :key="`${position.symbol}-${position.book}`" class="border-b border-ink-800 p-4 last:border-0">
          <div class="flex flex-wrap items-center gap-2 text-xs"><span class="tnum text-paper">{{ position.symbol }} / {{ position.book }}</span><span class="chip bg-warn-500/10 text-warn-500">保护状态未知</span><span class="text-mute-400">查询：{{ observed(position.read_status) }} · 本地基础币数量：{{ position.local_qty_base }}</span></div>
          <p class="mt-2 text-xs text-mute-500">{{ position.note }}</p>
          <p v-if="!position.legs.length" class="mt-2 text-xs text-mute-400">本次结果没有可展示的止损条件单；不能据此判断是否受保护。</p>
          <div v-for="(leg, index) in position.legs" :key="`${leg.algo_id}-${index}`" class="mt-2 rounded border border-ink-800 bg-ink-950 p-3 text-xs leading-relaxed text-mute-400">
            <div class="break-all">条件单 {{ leg.algo_id || '未知' }} · 状态 {{ leg.state }} · 归属未知</div>
            <div class="mt-1">方向 {{ leg.side }} / {{ leg.position_side }} · 触发价 {{ leg.stop_trigger }}（{{ leg.trigger_type }}）· 交易所数量 {{ leg.venue_size }} 张</div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
