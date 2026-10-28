<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import type { OrdersPayload } from "@/api/types";
import { fmtMoney, fmtNum, fmtTime, pnlClass } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";

const data = ref<OrdersPayload | null>(null);
const err = ref("");
const tab = ref<"open" | "fills">("open");

onMounted(async () => {
  try {
    data.value = await v1.orders();
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  }
});

const open = computed(() => data.value?.open ?? []);
const fills = computed(() => data.value?.fills ?? []);

const statusTone: Record<string, string> = {
  new: "bg-info-500/10 text-info-500",
  partial: "bg-warn-500/10 text-warn-500",
  pending: "bg-warn-500/10 text-warn-500",
  unknown: "bg-danger-600/10 text-danger-500",
};
</script>

<template>
  <div>
    <PageHeader title="订单 / 成交" subtitle="未结订单与最近成交。UNKNOWN 状态表示与交易所失联，需要人工确认。">
      <template #actions>
        <div class="flex rounded-lg border border-ink-600 p-0.5">
          <button
            class="rounded-md px-3 py-1 text-xs transition"
            :class="tab === 'open' ? 'bg-ink-700 text-paper' : 'text-mute-400'"
            @click="tab = 'open'"
          >
            未结 ({{ open.length }})
          </button>
          <button
            class="rounded-md px-3 py-1 text-xs transition"
            :class="tab === 'fills' ? 'bg-ink-700 text-paper' : 'text-mute-400'"
            @click="tab = 'fills'"
          >
            成交 ({{ fills.length }})
          </button>
        </div>
      </template>
    </PageHeader>

    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div v-if="tab === 'open'" class="card">
      <div v-if="open.length" class="overflow-x-auto">
        <table class="table-base">
          <thead>
            <tr><th>时间</th><th>标的</th><th>方向</th><th>类型</th><th class="text-right">数量</th><th class="text-right">已成交</th><th class="text-right">均价</th><th>状态</th><th>clOrdId</th></tr>
          </thead>
          <tbody>
            <tr v-for="o in open" :key="o.client_id">
              <td class="tnum whitespace-nowrap text-xs text-mute-400">{{ fmtTime(o.created_ms) }}</td>
              <td class="tnum text-xs">{{ o.symbol }}</td>
              <td class="text-xs" :class="o.side === 'buy' ? 'text-accent-400' : 'text-danger-500'">{{ o.side }}</td>
              <td class="text-xs text-mute-300">{{ o.order_type }}</td>
              <td class="tnum text-right text-xs">{{ o.qty }}</td>
              <td class="tnum text-right text-xs text-mute-300">{{ o.filled_qty }}</td>
              <td class="tnum text-right text-xs text-mute-300">{{ o.avg_px ? fmtMoney(o.avg_px) : '—' }}</td>
              <td><span class="chip" :class="statusTone[o.status] ?? 'bg-ink-700 text-mute-300'">{{ o.status }}</span></td>
              <td class="tnum text-[10px] text-mute-500">{{ o.client_id }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else title="没有未结订单" hint="所有订单都已成交或撤销。" />
    </div>

    <div v-else class="card">
      <div v-if="fills.length" class="overflow-x-auto">
        <table class="table-base">
          <thead>
            <tr><th>时间</th><th>标的</th><th>方向</th><th class="text-right">数量</th><th class="text-right">价格</th><th class="text-right">手续费</th><th>流动性</th></tr>
          </thead>
          <tbody>
            <tr v-for="f in fills" :key="`${f.ts_ms}-${f.order_client_id}`">
              <td class="tnum whitespace-nowrap text-xs text-mute-400">{{ fmtTime(f.ts_ms) }}</td>
              <td class="tnum text-xs">{{ f.symbol }}</td>
              <td class="text-xs" :class="f.side === 'buy' ? 'text-accent-400' : 'text-danger-500'">{{ f.side }}</td>
              <td class="tnum text-right text-xs">{{ fmtNum(f.qty) }}</td>
              <td class="tnum text-right text-xs text-mute-300">{{ fmtMoney(f.px) }}</td>
              <td class="tnum text-right text-xs" :class="pnlClass(-Number(f.fee))">{{ fmtMoney(f.fee, 4) }}</td>
              <td class="text-xs text-mute-400">{{ f.liquidity }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else title="没有成交记录" hint="策略还没有产生任何成交。" />
    </div>
  </div>
</template>
