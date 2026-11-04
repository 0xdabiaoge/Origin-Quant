<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import type { EChartsOption } from "echarts";
import { v1 } from "@/api/client";
import type { CandlesPayload, MarketQuote, Position } from "@/api/types";
import { fmtTime } from "@/api/format";
import EChart from "./EChart.vue";
import EmptyState from "./EmptyState.vue";

const props = withDefaults(defineProps<{ positions?: Position[]; compact?: boolean }>(), { positions: () => [], compact: false });
const quotes = ref<MarketQuote[]>([]);
const selected = ref("BTC-USDT-SWAP");
const timeframe = ref("15m");
const periods = [{ id: "15m", label: "15 分" }, { id: "1h", label: "1 小时" }, { id: "4h", label: "4 小时" }, { id: "1d", label: "日线" }];
const snapshot = ref<CandlesPayload | null>(null);
const busy = ref(false);
const error = ref("");
const quoteError = ref("");
const showMA = ref(true);
const showLevels = ref(true);
const search = ref("");
let epoch = 0;
let disposed = false;
let timer: number | undefined;
const quote = computed(() => quotes.value.find(q => q.symbol === selected.value));
const bars = computed(() => snapshot.value?.items ?? []);
const latest = computed(() => bars.value.at(-1));
const visibleQuotes = computed(() => quotes.value.filter(q => q.symbol.toLowerCase().includes(search.value.toLowerCase())));
const step = computed(() => ({ "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000 })[timeframe.value] ?? 900000);
const gaps = computed(() => bars.value.slice(1).filter((b, i) => b.ts_ms - bars.value[i]!.ts_ms !== step.value).length);
const price = (n: number | null | undefined) => n == null ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: n < 1 ? 6 : n < 100 ? 4 : 2 });
const change = (n: number | null | undefined) => n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
function spark(values: number[]) {
  if (values.length < 2) return "";
  const lo = Math.min(...values), span = Math.max(...values) - lo || 1;
  return values.map((v, i) => `${(i / (values.length - 1) * 68).toFixed(1)},${(22 - (v - lo) / span * 19).toFixed(1)}`).join(" ");
}
async function loadQuotes() {
  try {
    const result = await v1.marketSummary();
    if (disposed) return;
    quotes.value = result.items;
    quoteError.value = "";
    if (result.items.length && !result.items.some(q => q.symbol === selected.value)) selected.value = result.items[0]!.symbol;
  } catch (e) { if (!disposed) quoteError.value = e instanceof Error ? e.message : "行情列表加载失败"; }
}
async function loadCandles(clear = false) {
  const id = ++epoch;
  if (clear) snapshot.value = null;
  busy.value = true;
  try {
    const result = await v1.candles(selected.value, timeframe.value);
    if (id !== epoch || disposed) return;
    snapshot.value = result;
    error.value = "";
  } catch (e) {
    if (id === epoch && !disposed) error.value = e instanceof Error ? e.message : "K 线加载失败";
  } finally { if (id === epoch && !disposed) busy.value = false; }
}
function refresh() { void loadQuotes(); void loadCandles(); }
watch([selected, timeframe], () => { error.value = ""; void loadCandles(true); });
onMounted(() => { refresh(); timer = window.setInterval(refresh, 30000); });
onUnmounted(() => { disposed = true; epoch++; window.clearInterval(timer); });

function average(period: number) {
  return bars.value.map((_, i) => {
    if (i < period - 1 || bars.value[i]!.ts_ms - bars.value[i - period + 1]!.ts_ms !== (period - 1) * step.value) return null;
    return bars.value.slice(i - period + 1, i + 1).reduce((sum, b) => sum + b.close, 0) / period;
  });
}
const option = computed<EChartsOption>(() => {
  const rows = bars.value;
  const labels = rows.map(b => b.ts_ms);
  const levels = showLevels.value ? props.positions.filter(p => p.symbol === selected.value).flatMap(p => [
    { name: `${p.book} 本地均价`, yAxis: Number(p.avg_px), lineStyle: { color: "#60a5fa", type: "dashed" as const } },
    ...(p.stop_px ? [{ name: `${p.book} 本地止损`, yAxis: Number(p.stop_px), lineStyle: { color: "#fb7185", type: "dashed" as const } }] : []),
  ]).filter(l => Number.isFinite(l.yAxis) && l.yAxis > 0) : [];
  return {
    animation: false,
    backgroundColor: "transparent",
    legend: { top: 2, left: 10, textStyle: { color: "#93a4ba", fontSize: 10 }, itemWidth: 14, itemHeight: 3, data: showMA.value ? ["MA 7", "MA 25", "MA 99"] : [] },
    grid: [{ left: 12, right: 80, top: 40, height: "57%" }, { left: 12, right: 80, top: "73%", height: "12%" }],
    tooltip: {
      trigger: "axis", renderMode: "richText", backgroundColor: "#121d2b", borderColor: "#304155", textStyle: { color: "#e8eef6", fontSize: 11 },
      axisPointer: { type: "cross" },
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])?.[0]?.dataIndex;
        const b = i === undefined ? undefined : rows[i];
        return b ? `${fmtTime(b.ts_ms)}\n开 ${price(b.open)}   高 ${price(b.high)}\n收 ${price(b.close)}   低 ${price(b.low)}\n成交量 ${price(b.volume)}（交易所原始单位）` : "";
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    xAxis: [0, 1].map(gridIndex => ({
      type: "category" as const, gridIndex, data: labels, boundaryGap: true,
      axisLine: { lineStyle: { color: "#243247" } }, axisTick: { show: false },
      axisLabel: { show: gridIndex === 1, color: "#71849c", fontSize: 10, hideOverlap: true,
        formatter: (v: string) => new Date(Number(v)).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) },
      splitLine: { show: false },
    })),
    yAxis: [0, 1].map(gridIndex => ({
      type: "value" as const, gridIndex, scale: true, position: "right" as const,
      splitNumber: gridIndex === 1 ? 2 : 5,
      axisLabel: { color: "#71849c", fontSize: 10, formatter: (v: number) => gridIndex ? (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)) : price(v) },
      splitLine: { show: gridIndex === 0, lineStyle: { color: "#1c2a3b", type: "dashed" as const } },
    })),
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: Math.max(0, 100 - 100 * 90 / Math.max(1, rows.length)), end: 100 },
      { type: "slider", xAxisIndex: [0, 1], bottom: 4, height: 18, borderColor: "transparent", backgroundColor: "#0b1420", fillerColor: "rgba(52,211,153,.08)", textStyle: { color: "#71849c" }, showDetail: false, handleStyle: { color: "#40546a" } },
    ],
    series: [
      { name: "K 线", type: "candlestick", data: rows.map(b => [b.open, b.close, b.low, b.high]),
        itemStyle: { color: "#34d399", color0: "#fb7185", borderColor: "#34d399", borderColor0: "#fb7185" },
        markLine: { symbol: ["none", "none"], silent: true, label: { formatter: "{b}", color: "#c5d2e3", fontSize: 10, position: "insideEndTop" }, data: levels },
      },
      ...(showMA.value ? [7, 25, 99].map((p, i) => ({
        name: `MA ${p}`, type: "line" as const, data: average(p), showSymbol: false, connectNulls: false,
        lineStyle: { width: 1.2, color: ["#fbbf24", "#60a5fa", "#c4a5ff"][i] },
      })) : []),
      { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1,
        data: rows.map(b => ({ value: b.volume, itemStyle: { color: b.close >= b.open ? "#34d39966" : "#fb718566" } })),
      },
    ],
  };
});
</script>

<template>
  <section class="card market-terminal overflow-hidden" aria-label="币种行情与 K 线">
    <div class="terminal-heading">
      <div><div class="eyebrow">MARKET WORKSPACE</div><h2 class="mt-1 text-base font-semibold">行情工作台 <span class="ml-2 text-xs font-normal text-mute-400">本地已收盘行情</span></h2></div>
      <button class="btn btn-ghost text-xs" :disabled="busy" @click="refresh">{{ busy ? '更新中…' : '↻ 刷新行情' }}</button>
    </div>
    <div class="grid min-w-0 xl:grid-cols-[minmax(0,1fr)_244px]">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center justify-between gap-4 px-4 pt-4 sm:px-5">
          <div class="flex items-center gap-3">
            <span class="coin-avatar">{{ selected.split('-')[0]?.slice(0, 1) }}</span>
            <div><div class="text-sm font-semibold">{{ selected.split('-')[0] }} <span class="text-xs font-normal text-mute-500">/ USDT · {{ selected.endsWith('SWAP') ? '永续' : '现货' }}</span></div><div class="tnum mt-1 text-xl font-semibold">{{ price(quote?.close ?? latest?.close) }} <span class="font-sans text-[9px] font-normal text-mute-500">{{ snapshot?.base_timeframe ?? '—' }} 收盘</span></div></div>
            <span class="chip" :class="(quote?.change_24h_pct ?? 0) >= 0 ? 'bg-accent-500/10 text-accent-400' : 'bg-danger-500/10 text-danger-500'">{{ change(quote?.change_24h_pct) }} <span class="text-[9px] opacity-60">24H</span></span>
          </div>
          <div class="flex gap-5 text-[11px] text-mute-500"><div>24H 最高<div class="tnum mt-1 text-mute-300">{{ price(quote?.high_24h) }}</div></div><div>24H 最低<div class="tnum mt-1 text-mute-300">{{ price(quote?.low_24h) }}</div></div></div>
        </div>
        <div class="mx-4 mt-4 flex flex-wrap items-center justify-between gap-3 border-y border-ink-700/70 py-2 sm:mx-5">
          <div class="flex gap-1" aria-label="K 线周期"><button v-for="p in periods" :key="p.id" class="chart-tab" :class="{ 'chart-tab-active': timeframe === p.id }" :aria-pressed="timeframe === p.id" @click="timeframe = p.id">{{ p.label }}</button></div>
          <div class="flex gap-3 text-[11px] text-mute-400"><label class="flex items-center gap-1.5"><input v-model="showMA" type="checkbox" class="accent-accent-500">均线</label><label class="flex items-center gap-1.5"><input v-model="showLevels" type="checkbox" class="accent-accent-500">本地持仓线</label></div>
        </div>
        <p v-if="error" role="alert" class="mx-4 mt-3 text-xs text-danger-500">{{ error }}{{ snapshot ? '；保留上次快照，不能视为最新行情。' : '' }}</p>
        <div v-if="busy && !snapshot" class="flex h-[380px] animate-pulse items-center justify-center text-sm text-mute-400">正在读取 {{ selected }} 的收盘行情…</div>
        <EChart v-else-if="bars.length" :option="option" :reset-key="`${selected}:${timeframe}`" :label="`${selected} ${timeframe} 收盘 K 线及成交量，支持缩放与拖动`" :height="compact ? '380px' : '480px'" />
        <EmptyState v-else title="这个周期还没有完整 K 线" hint="高周期需要足够的连续收盘数据；可切换 15 分钟查看，或等待行情采集。" />
        <div class="flex flex-wrap items-center justify-between gap-2 border-t border-ink-800 px-4 py-3 text-[10px] text-mute-500">
          <span>{{ bars.length }} 根已收盘 · {{ snapshot?.last_bar_ms ? fmtTime(snapshot.last_bar_ms) : '尚无数据' }} · 时间为本机时区</span>
          <span :class="error || snapshot?.stale || gaps ? 'text-warn-500' : 'text-mute-400'">{{ error ? '刷新失败' : gaps ? `存在 ${gaps} 处时间缺口` : snapshot?.stale ? '数据待更新' : snapshot ? '● 收盘数据正常' : '等待数据' }}</span>
          <span v-if="positions.some(p => p.symbol === selected)">持仓线为本地记录，需核对交易所保护单。</span>
        </div>
      </div>
      <aside class="border-t border-ink-700 xl:border-t-0 xl:border-l" aria-label="币种观察列表">
        <div class="flex items-center justify-between px-4 pt-4"><h3 class="text-xs font-medium">市场观察</h3><span class="text-[10px] text-mute-500">24H 涨跌</span></div>
        <div class="p-3"><input v-model="search" class="input !py-1.5 !text-xs" aria-label="搜索币种" placeholder="搜索币种，如 BTC"></div>
        <p v-if="quoteError" class="px-4 pb-3 text-[11px] text-warn-500">{{ quoteError }}；列表可能为旧快照。</p>
        <div class="grid grid-cols-1 overflow-y-auto sm:grid-cols-2 xl:grid-cols-1" :class="compact ? 'max-h-[400px]' : 'max-h-[530px]'">
          <button v-for="q in visibleQuotes" :key="q.symbol" class="watchlist-row" :class="{ 'watchlist-selected': selected === q.symbol }" :aria-pressed="selected === q.symbol" @click="selected = q.symbol">
            <div class="flex items-center justify-between gap-2"><span class="text-xs font-medium">{{ q.symbol.split('-')[0] }} <span class="text-[9px] text-mute-500">{{ q.symbol.endsWith('SWAP') ? '永续' : '现货' }}</span></span><span class="tnum text-[11px]" :class="q.stale ? 'text-warn-500' : (q.change_24h_pct ?? 0) >= 0 ? 'text-accent-400' : 'text-danger-500'">{{ q.stale ? '待更新' : change(q.change_24h_pct) }}</span></div>
            <div class="mt-1.5 flex items-center justify-between"><span class="tnum text-xs text-mute-300">{{ price(q.close) }}</span><svg viewBox="0 0 70 25" class="h-6 w-[70px]" aria-hidden="true"><polyline :points="spark(q.sparkline)" fill="none" :stroke="(q.change_24h_pct ?? 0) >= 0 ? '#34d399' : '#fb7185'" stroke-width="1.3" /></svg></div>
          </button>
        </div>
        <p v-if="!visibleQuotes.length" class="p-4 text-xs text-mute-500">{{ quotes.length ? '没有匹配的币种' : '等待行情列表' }}</p>
        <p class="px-4 py-3 text-[10px] leading-relaxed text-mute-500">每 30 秒检查本地快照。行情由交易调度器采集，价格为收盘价。</p>
      </aside>
    </div>
  </section>
</template>
