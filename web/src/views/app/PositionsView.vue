<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import type { PositionsPayload } from "@/api/types";
import { fmtMoney, fmtPct, fmtSigned, pnlClass, qtyClass, fmtTime } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";
import StatCard from "@/components/StatCard.vue";
import EmptyState from "@/components/EmptyState.vue";

const data = ref<PositionsPayload | null>(null);
const err = ref("");

onMounted(async () => {
  try {
    data.value = await v1.positions();
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  }
});

const rows = computed(() => data.value?.positions ?? []);
const localBookAvailable = computed(() => !!data.value && (data.value.equity !== null || rows.value.length > 0));
const carry = computed(() => rows.value.filter((p) => p.book === "carry"));
const directional = computed(() => rows.value.filter((p) => p.book !== "carry"));

function unrealized(p: { qty: string; avg_px: string; mark_px?: string | null }): string | null {
  if (!p.mark_px) return null;
  const diff = Number(p.mark_px) - Number(p.avg_px);
  const v = diff * Number(p.qty);
  return Number.isFinite(v) ? v.toFixed(2) : null;
}
</script>

<template>
  <div>
    <PageHeader title="持仓" subtitle="本地账本按账本分组；标记和盈亏使用本地最近可用价格估算，不代表交易所实时状态。" />

    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatCard label="净值" :value="data?.equity ? fmtMoney(data.equity) : '—'" />
      <StatCard label="现金" :value="data?.cash ? fmtMoney(data.cash) : '—'" />
      <StatCard label="热度" :value="data?.heat ? fmtPct(data.heat) : '—'" hint="风险预算占用比例" />
      <StatCard label="未平风险" :value="data?.open_risk ? fmtMoney(data.open_risk) : '—'" hint="基于本地止损估算，不含滑点及未设止损仓位" />
    </div>
    <p v-if="rows.some((position) => !position.stop_px)" class="mt-3 text-xs text-warn-500">部分本地持仓没有止损价；未平风险数值不包含这些仓位，交易所保护状态也需要单独核对。</p>

    <div v-for="group in [{ title: '套利账本 (carry)', list: carry }, { title: '方向性账本 (directional)', list: directional }]" :key="group.title" class="card mt-3">
      <div class="flex items-center justify-between border-b border-ink-800 px-4 py-3">
        <span class="label">{{ group.title }}</span>
        <span class="text-[11px] text-mute-500">{{ localBookAvailable ? `${group.list.length} 条` : '未知' }}</span>
      </div>
      <div v-if="group.list.length" class="overflow-x-auto">
        <table class="table-base">
          <thead>
            <tr>
              <th>标的</th><th class="text-right">数量</th><th class="text-right">均价</th>
              <th class="text-right">标记</th><th class="text-right">未实现</th>
              <th class="text-right">止损</th><th class="text-right">已实现</th>
              <th class="text-right">资金费</th><th>开仓时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in group.list" :key="p.symbol">
              <td class="tnum text-xs">{{ p.symbol }}</td>
              <td class="tnum text-right text-xs" :class="qtyClass(p.qty)">{{ p.qty }}</td>
              <td class="tnum text-right text-xs text-mute-300">{{ fmtMoney(p.avg_px) }}</td>
              <td class="tnum text-right text-xs text-mute-300">{{ p.mark_px ? fmtMoney(p.mark_px) : '—' }}</td>
              <td class="tnum text-right text-xs" :class="pnlClass(unrealized(p))">{{ unrealized(p) === null ? '—' : fmtSigned(unrealized(p)) }}</td>
              <td class="tnum text-right text-xs text-mute-400">{{ p.stop_px ? fmtMoney(p.stop_px) : '—' }}</td>
              <td class="tnum text-right text-xs" :class="pnlClass(p.realized_pnl)">{{ fmtSigned(p.realized_pnl) }}</td>
              <td class="tnum text-right text-xs" :class="pnlClass(p.funding_net)">{{ fmtSigned(p.funding_net) }}</td>
              <td class="tnum whitespace-nowrap text-xs text-mute-500">{{ fmtTime(p.opened_ms) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else :title="localBookAvailable ? '该账本无本地持仓记录' : '持仓状态未知'" hint="请结合交易所实际持仓核对。" />
    </div>
  </div>
</template>
