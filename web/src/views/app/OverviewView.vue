<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { v1 } from "@/api/client";
import type { Overview } from "@/api/types";
import { fmtAgo, fmtMoney, fmtPct, fmtSigned, pnlClass, qtyClass, fmtTime } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";
import StatCard from "@/components/StatCard.vue";
import EmptyState from "@/components/EmptyState.vue";
import EChart from "@/components/EChart.vue";
import MarketTerminal from "@/components/MarketTerminal.vue";
import { useToast } from "@/components/useToast";
import { useAuthStore } from "@/stores/auth";
import type { EChartsOption } from "echarts";

const data = ref<Overview | null>(null);
const loading = ref(true);
const err = ref("");
const busy = ref(false);
const toast = useToast();
const auth = useAuthStore();
const lastSuccessfulRefresh = ref<number | null>(null);
let timer: number | undefined;

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    data.value = await v1.overview();
    lastSuccessfulRefresh.value = Date.now();
    err.value = "";
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void load();
  timer = window.setInterval(() => void load(true), 15000);
});
onUnmounted(() => window.clearInterval(timer));

async function runCycle() {
  busy.value = true;
  try {
    const res = await v1.cycle();
    const skipped = res.skipped ? `（跳过：${res.skipped}）` : "";
    const errors = Array.isArray(res.errors) ? res.errors : [];
    const summary = `周期已返回 · 意图 ${res.intents ?? '未知'} / 通过 ${res.approved ?? '未知'} / 成交 ${res.fills ?? '未知'}${skipped}`;
    if (errors.length) toast.error(`${summary}；存在 ${errors.length} 项错误，请查看日志和交易所状态`);
    else if (res.skipped) toast.info(summary);
    else toast.ok(summary);
    await load(true);
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "执行失败");
  } finally {
    busy.value = false;
  }
}

const series = computed(() => data.value?.equity_series ?? []);
const lastPoint = computed(() => series.value.at(-1));
const firstPoint = computed(() => series.value[0]);

const equityOption = computed<EChartsOption>(() => {
  const pts = series.value;
  return {
    grid: { left: 64, right: 16, top: 20, bottom: 30 },
    xAxis: {
      type: "category",
      axisLabel: { color: "#71849c", fontSize: 10, formatter: (v: string) => new Date(Number(v)).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" }) },
      axisLine: { show: false }, axisTick: { show: false },
      boundaryGap: false,
      data: pts.map((p) => p.ts_ms),
    },
    yAxis: { type: "value", scale: true,
      min: v => v.min - Math.max(1, (v.max - v.min) * 0.1, Math.abs(v.min) * 0.001),
      max: v => v.max + Math.max(1, (v.max - v.min) * 0.1, Math.abs(v.max) * 0.001),
      axisLabel: { color: "#71849c", fontSize: 10 }, splitLine: { lineStyle: { color: "#1c2a3b", type: "dashed" } } },
    tooltip: {
      trigger: "axis",
      renderMode: "richText",
      backgroundColor: "#0f141b",
      borderColor: "#1c2530",
      textStyle: { color: "#e8eef6", fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = params as { dataIndex: number }[];
        const p = pts[arr?.[0]?.dataIndex ?? 0];
        if (!p) return "";
        return `${fmtTime(p.ts_ms)}\n净值 ${fmtMoney(p.equity)}\n回撤 ${fmtPct(p.drawdown)}`;
      },
    },
    series: [
      {
        type: "line",
        data: pts.map((p) => Number(p.equity)),
        showSymbol: pts.length === 1,
        symbolSize: 6,
        itemStyle: { color: "#34d399" },
        lineStyle: { width: 2, color: "#34d399" },
        areaStyle: { color: "rgba(52,211,153,0.10)" },
      },
    ],
  };
});

const riskOption = computed<EChartsOption>(() => ({
  grid: { left: 42, right: 12, top: 24, bottom: 20 },
  legend: { top: 0, textStyle: { color: "#93a4ba", fontSize: 10 }, itemHeight: 3 },
  tooltip: { trigger: "axis", renderMode: "richText", valueFormatter: (v: unknown) => `${Number(v).toFixed(2)}%` },
  xAxis: { type: "category", show: false, data: series.value.map(p => fmtTime(p.ts_ms)) },
  yAxis: { type: "value", min: 0, axisLabel: { color: "#71849c", fontSize: 9, formatter: "{value}%" }, splitLine: { lineStyle: { color: "#1c2a3b" } } },
  series: [
    { name: "回撤", type: "line", showSymbol: series.value.length === 1, data: series.value.map(p => Number(p.drawdown) * 100), lineStyle: { color: "#fb7185", width: 1.5 }, itemStyle: { color: "#fb7185" }, areaStyle: { color: "#fb718514" } },
    { name: "风险热度", type: "line", showSymbol: series.value.length === 1, data: series.value.map(p => p.heat == null ? null : Number(p.heat) * 100), lineStyle: { color: "#60a5fa", width: 1.5 }, itemStyle: { color: "#60a5fa" } },
  ],
}));

const sessionPnl = computed(() => {
  if (!lastPoint.value || !firstPoint.value) return null;
  return Number(lastPoint.value.equity) - Number(firstPoint.value.equity);
});

const stats = computed(() => data.value?.stats);
const positions = computed(() => data.value?.positions ?? []);
const localBookAvailable = computed(() => !!data.value && (data.value.equity !== null || positions.value.length > 0));
const carryCount = computed(() => positions.value.filter((p) => p.book === "carry").length);
const dirCount = computed(() => positions.value.filter((p) => p.book !== "carry").length);
</script>

<template>
  <div>
    <PageHeader title="交易总览" :subtitle="`市场、策略与账户，尽在掌握 · 最近周期 ${fmtAgo(data?.last_cycle_ms)}`">
      <template #actions>
        <button v-if="auth.can('trader')" class="btn" :disabled="busy" @click="runCycle">
          {{ busy ? "执行中…" : "手动跑一个周期" }}
        </button>
        <button class="btn btn-ghost" @click="load()">刷新</button>
      </template>
    </PageHeader>

    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">
      {{ err }}<span v-if="lastSuccessfulRefresh">；当前仍显示 {{ fmtTime(lastSuccessfulRefresh) }} 的旧快照，请勿视为实时状态。</span>
    </p>

    <p v-if="data?.positions.length" class="mb-3 text-xs text-mute-500">净值使用本地可用价格估算；价格缺失或过期时显示未知，请核对交易所实际权益。</p>
    <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatCard label="净值" :value="data?.equity ? fmtMoney(data.equity) : '—'" :hint="`现金 ${data?.cash ? fmtMoney(data.cash) : '—'}`" />
      <StatCard
        label="区间盈亏"
        :value="sessionPnl === null ? '—' : fmtSigned(sessionPnl)"
        :tone="sessionPnl === null ? 'neutral' : sessionPnl >= 0 ? 'up' : 'down'"
        hint="当前净值曲线首尾差"
      />
      <StatCard
        label="当前回撤"
        :value="lastPoint ? fmtPct(lastPoint.drawdown) : '—'"
        :tone="lastPoint && Number(lastPoint.drawdown) > 0.05 ? 'down' : 'neutral'"
        :hint="lastPoint ? `热度 ${fmtPct(lastPoint.heat)}` : ''"
      />
      <StatCard
        label="持仓"
        :value="data ? String(positions.length) : '—'"
        :hint="data ? `套利 ${carryCount} · 方向 ${dirCount}` : ''"
      />
    </div>

    <MarketTerminal class="mt-5" :positions="positions" compact />

    <div class="mt-5 grid gap-4 lg:grid-cols-[1.6fr_1fr]">
      <div class="card card-pad">
        <div class="mb-1 flex items-center justify-between">
          <span class="label">净值曲线</span>
          <span class="text-[11px] text-mute-500">{{ series.length }} 个采样点</span>
        </div>
        <EChart v-if="series.length" :option="equityOption" height="270px" :loading="loading" />
        <p v-if="series.length === 1" class="mt-2 text-[11px] text-mute-500">目前只有 1 个真实采样点，净值曲线将随交易周期逐步累计。</p>
        <EmptyState v-else title="还没有净值数据" hint="跑一个周期或等待调度器采样后，这里会出现曲线。" />
      </div>

      <div class="card card-pad">
        <div class="label mb-3">风险与交易表现</div>
        <EChart v-if="series.length" :option="riskOption" height="140px" />
        <dl class="space-y-2.5 text-sm">
          <div class="flex items-baseline justify-between">
            <dt class="text-mute-400">成交笔数</dt>
            <dd class="tnum">{{ stats?.trades ?? '—' }}</dd>
          </div>
          <div class="flex items-baseline justify-between">
            <dt class="text-mute-400">胜率</dt>
            <dd class="tnum">{{ stats ? fmtPct(stats.win_rate) : '—' }}</dd>
          </div>
          <div class="flex items-baseline justify-between">
            <dt class="text-mute-400">净盈亏</dt>
            <dd class="tnum" :class="pnlClass(stats?.net_pnl)">{{ stats ? fmtSigned(stats.net_pnl) : '—' }}</dd>
          </div>
          <div class="flex items-baseline justify-between">
            <dt class="text-mute-400">手续费</dt>
            <dd class="tnum text-mute-300">{{ stats ? fmtMoney(stats.fees) : '—' }}</dd>
          </div>
          <div class="flex items-baseline justify-between">
            <dt class="text-mute-400">盈亏比</dt>
            <dd class="tnum">{{ stats?.profit_factor ?? '—' }}</dd>
          </div>
        </dl>
        <p class="mt-4 border-t border-ink-800 pt-3 text-[11px] leading-relaxed text-mute-500">
          手续费与资金费会显著侵蚀边际。看到负期望值时，先看成本，再看策略。
        </p>
      </div>
    </div>

    <div class="mt-3 grid gap-3 lg:grid-cols-2">
      <div class="card">
        <div class="flex items-center justify-between border-b border-ink-800 px-4 py-3">
          <span class="label">持仓</span>
          <span class="text-[11px] text-mute-500">{{ localBookAvailable ? `${positions.length} 条` : '未知' }}</span>
        </div>
        <div v-if="positions.length" class="overflow-x-auto">
          <table class="table-base">
            <thead>
              <tr>
                <th>标的</th><th>账本</th><th class="text-right">数量</th>
                <th class="text-right">均价</th><th class="text-right">已实现</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in positions" :key="`${p.symbol}-${p.book}`">
                <td class="tnum text-xs">{{ p.symbol }}</td>
                <td>
                  <span class="chip" :class="p.book === 'carry' ? 'bg-info-500/10 text-info-500' : 'bg-warn-500/10 text-warn-500'">
                    {{ p.book }}
                  </span>
                </td>
                <td class="tnum text-right text-xs" :class="qtyClass(p.qty)">{{ p.qty }}</td>
                <td class="tnum text-right text-xs text-mute-300">{{ fmtMoney(p.avg_px) }}</td>
                <td class="tnum text-right text-xs" :class="pnlClass(p.realized_pnl)">{{ fmtSigned(p.realized_pnl) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState v-else :title="localBookAvailable ? '没有本地持仓记录' : '持仓状态未知'" hint="请结合交易所实际持仓核对。" />
      </div>

      <div class="card">
        <div class="flex items-center justify-between border-b border-ink-800 px-4 py-3">
          <span class="label">最近决策</span>
          <span class="text-[11px] text-mute-500">{{ data ? `${data.recent_decisions.length} 条` : '未知' }}</span>
        </div>
        <div v-if="data?.recent_decisions.length" class="max-h-[320px] overflow-y-auto">
          <table class="table-base">
            <thead>
              <tr><th>时间</th><th>策略</th><th>动作</th><th>结果</th></tr>
            </thead>
            <tbody>
              <tr v-for="d in data.recent_decisions" :key="d.id">
                <td class="tnum whitespace-nowrap text-xs text-mute-400">{{ fmtTime(d.ts_ms) }}</td>
                <td class="text-xs">{{ d.strategy_id }}</td>
                <td class="text-xs text-mute-300">{{ d.action }}</td>
                <td>
                  <span class="chip" :class="d.approved ? 'bg-accent-600/10 text-accent-400' : 'bg-danger-600/10 text-danger-500'">
                    {{ d.approved ? "通过" : "拒绝" }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState v-else title="没有决策记录" hint="跑一个周期后这里会出现策略的每次判断。" />
      </div>
    </div>
  </div>
</template>
