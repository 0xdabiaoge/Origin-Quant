<script setup lang="ts">
import { onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import type { AdminOverview } from "@/api/types";
import { fmtMoney, fmtSigned, fmtTime, fmtAgo, fmtPct, pnlClass } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";
import StatCard from "@/components/StatCard.vue";

const data = ref<AdminOverview | null>(null);
const err = ref("");

onMounted(async () => {
  try {
    data.value = await v1.adminOverview();
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  }
});

function uptime(s: number): string {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d} 天 ${h} 小时` : h > 0 ? `${h} 小时 ${m} 分` : `${m} 分`;
}
</script>

<template>
  <div>
    <PageHeader title="系统概览" subtitle="引擎的实时状态与数据规模。" />
    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatCard label="版本" :value="data?.version ?? '—'" :hint="`模式 ${data?.mode ?? '—'}`" />
      <StatCard label="运行时长" :value="data ? uptime(data.uptime_s) : '—'" :hint="data?.scheduler ? '调度器已启用' : '调度器未启用'" />
      <StatCard label="事件总数" :value="String(data?.counts.events ?? 0)" :hint="data?.last_event_ms ? `最新 ${fmtAgo(data.last_event_ms)}` : ''" />
      <StatCard label="配置指纹" :value="data?.config_hash ? data.config_hash.slice(0, 10) : '—'" hint="配置变更会改变此指纹" />
    </div>

    <div class="mt-3 grid gap-3 lg:grid-cols-2">
      <div class="card card-pad">
        <div class="label mb-3">表记录数</div>
        <dl class="space-y-2 text-sm">
          <div v-for="(v, k) in data?.counts ?? {}" :key="k" class="flex items-baseline justify-between">
            <dt class="text-mute-400">{{ k }}</dt>
            <dd class="tnum">{{ v }}</dd>
          </div>
        </dl>
      </div>

      <div class="card card-pad">
        <div class="label mb-3">事件类型分布</div>
        <div v-if="data?.events_by_type.length" class="space-y-1.5">
          <div v-for="e in data.events_by_type" :key="e.type" class="flex items-center gap-3">
            <span class="w-40 shrink-0 truncate text-xs text-mute-300">{{ e.type }}</span>
            <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-800">
              <div class="h-full rounded-full bg-accent-600/60" :style="{ width: `${(e.n / (data?.counts.events || 1)) * 100}%` }" />
            </div>
            <span class="tnum w-12 shrink-0 text-right text-xs text-mute-400">{{ e.n }}</span>
          </div>
        </div>
        <div v-else class="text-xs text-mute-500">还没有事件</div>
      </div>
    </div>

    <div class="card card-pad mt-3">
      <div class="label mb-3">交易统计</div>
      <div class="grid grid-cols-2 gap-4 sm:grid-cols-5">
        <div>
          <div class="text-[11px] text-mute-500">成交笔数</div>
          <div class="tnum mt-1 text-lg">{{ data?.stats.trades ?? 0 }}</div>
        </div>
        <div>
          <div class="text-[11px] text-mute-500">胜率</div>
          <div class="tnum mt-1 text-lg">{{ data ? fmtPct(data.stats.win_rate) : '—' }}</div>
        </div>
        <div>
          <div class="text-[11px] text-mute-500">净盈亏</div>
          <div class="tnum mt-1 text-lg" :class="pnlClass(data?.stats.net_pnl)">{{ data ? fmtSigned(data.stats.net_pnl) : '—' }}</div>
        </div>
        <div>
          <div class="text-[11px] text-mute-500">手续费</div>
          <div class="tnum mt-1 text-lg text-mute-300">{{ data ? fmtMoney(data.stats.fees) : '—' }}</div>
        </div>
        <div>
          <div class="text-[11px] text-mute-500">盈亏比</div>
          <div class="tnum mt-1 text-lg">{{ data?.stats.profit_factor ?? '—' }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
