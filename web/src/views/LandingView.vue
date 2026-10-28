<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { v1 } from "@/api/client";
import type { PublicMetrics } from "@/api/types";
import { fmtMoney, fmtPct, fmtSigned } from "@/api/format";
import EChart from "@/components/EChart.vue";
import type { EChartsOption } from "echarts";

const metrics = ref<PublicMetrics | null>(null);
const failed = ref(false);

onMounted(async () => {
  try {
    metrics.value = await v1.metrics();
  } catch {
    failed.value = true;
  }
});

const equityOption = computed<EChartsOption>(() => {
  const pts = metrics.value?.equity ?? [];
  return {
    grid: { left: 8, right: 8, top: 8, bottom: 8, containLabel: false },
    xAxis: { type: "category", show: false, data: pts.map((p) => p.ts_ms) },
    yAxis: { type: "value", show: false, scale: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#0f141b",
      borderColor: "#1c2530",
      textStyle: { color: "#e8eef6", fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = params as { dataIndex: number }[];
        const i = arr?.[0]?.dataIndex ?? 0;
        const p = pts[i];
        if (!p) return "";
        return `${new Date(p.ts_ms).toLocaleString("zh-CN", { hour12: false })}<br/>净值 ${fmtMoney(p.equity)}`;
      },
    },
    series: [
      {
        type: "line",
        data: pts.map((p) => Number(p.equity)),
        showSymbol: false,
        lineStyle: { width: 1.6, color: "#34d399" },
        areaStyle: { color: "rgba(52,211,153,0.10)" },
      },
    ],
  };
});

const last = computed(() => metrics.value?.equity.at(-1));
const stats = computed(() => metrics.value?.stats);

const features = [
  {
    title: "事件溯源账本",
    body: "每一笔意图、风控裁决、订单与成交都写入同一条追加事件流，投影与事件在同一事务内提交，面板永远不会和账本打架。",
  },
  {
    title: "纯函数策略内核",
    body: "策略不读系统时钟、不用随机数、不做 IO。同样的输入永远得到同样的输出，回测与实盘跑的是同一份代码。",
  },
  {
    title: "双账本 · 独立风控",
    body: "套利与方向性交易分账管理，各自有保证金与风险预算；平仓走结构性闸门，不被开仓闸门挡住。",
  },
  {
    title: "Decimal 金额 · UTC 毫秒",
    body: "金额与数量全程 Decimal，时间全程 UTC 整数毫秒。没有浮点误差，没有时区歧义。",
  },
];
</script>

<template>
  <div class="min-h-screen bg-ink-950">
    <!-- nav -->
    <header class="sticky top-0 z-30 border-b border-ink-800/80 bg-ink-950/80 backdrop-blur">
      <div class="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
        <div class="flex items-center gap-2.5">
          <svg viewBox="0 0 32 32" class="h-7 w-7">
            <rect width="32" height="32" rx="8" fill="#0f141b" />
            <path
              d="M8 22 L14 12 L19 17 L24 9"
              stroke="#34d399"
              stroke-width="2.5"
              fill="none"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          <span class="text-sm font-semibold tracking-tight">Origin Quant</span>
        </div>
        <nav class="flex items-center gap-1 text-sm">
          <RouterLink to="/docs" class="btn btn-ghost text-mute-300">文档</RouterLink>
          <RouterLink to="/login" class="btn btn-ghost text-mute-300">登录</RouterLink>
          <RouterLink to="/app" class="btn btn-primary">进入工作台</RouterLink>
        </nav>
      </div>
    </header>

    <!-- hero -->
    <section class="mx-auto max-w-6xl px-4 pb-16 pt-14 sm:px-6 sm:pt-20">
      <div class="grid items-center gap-10 lg:grid-cols-[1.1fr_1fr]">
        <div>
          <div class="chip mb-5 border border-accent-600/40 bg-accent-600/10 text-accent-400">
            <span class="h-1.5 w-1.5 rounded-full bg-accent-500" />
            {{ metrics?.mode === "live" ? "实盘运行中" : "纸面交易模式" }}
          </div>

          <h1 class="text-3xl font-semibold leading-tight tracking-tight sm:text-4xl lg:text-5xl">
            一个操作员，
            <span class="text-accent-400">一整条交易链路</span>
          </h1>
          <p class="mt-5 max-w-xl text-sm leading-relaxed text-mute-300 sm:text-base">
            信号、风控、执行、对账、审计全部收敛在单一进程内。没有分布式一致性假设，没有跨服务时钟漂移，
            也不需要相信任何一层的中间状态。
          </p>

          <div class="mt-8 flex flex-wrap gap-3">
            <RouterLink to="/app" class="btn btn-primary px-5 py-2.5">打开工作台</RouterLink>
            <RouterLink to="/docs" class="btn px-5 py-2.5">阅读文档</RouterLink>
          </div>

          <div v-if="stats && stats.trades > 0" class="mt-9 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
            <div>
              <div class="label">成交笔数</div>
              <div class="tnum mt-1 text-lg font-semibold">{{ stats.trades }}</div>
            </div>
            <div>
              <div class="label">胜率</div>
              <div class="tnum mt-1 text-lg font-semibold">{{ fmtPct(stats.win_rate) }}</div>
            </div>
            <div>
              <div class="label">净盈亏</div>
              <div class="tnum mt-1 text-lg font-semibold" :class="Number(stats.net_pnl) >= 0 ? 'text-accent-400' : 'text-danger-500'">
                {{ fmtSigned(stats.net_pnl) }}
              </div>
            </div>
            <div>
              <div class="label">盈亏比</div>
              <div class="tnum mt-1 text-lg font-semibold">{{ stats.profit_factor ?? "—" }}</div>
            </div>
          </div>
        </div>

        <div class="card card-pad">
          <div class="flex items-baseline justify-between">
            <div>
              <div class="label">当前净值</div>
              <div class="tnum mt-1.5 text-3xl font-semibold">
                {{ last ? fmtMoney(last.equity) : "—" }}
              </div>
            </div>
            <div class="text-right">
              <div class="label">回撤</div>
              <div class="tnum mt-1.5 text-sm text-danger-500">
                {{ last ? fmtPct(last.drawdown) : "—" }}
              </div>
            </div>
          </div>
          <div class="mt-4">
            <EChart v-if="!failed && (metrics?.equity?.length ?? 0) > 1" :option="equityOption" height="180px" />
            <div v-else class="flex h-[180px] items-center justify-center text-xs text-mute-500">
              {{ failed ? "无法读取公开指标" : "等待净值数据…" }}
            </div>
          </div>
          <p class="mt-3 border-t border-ink-800 pt-3 text-[11px] leading-relaxed text-mute-500">
            数据来自本机引擎的公开只读接口，不含持仓与订单。历史表现不代表未来收益。
          </p>
        </div>
      </div>
    </section>

    <!-- features -->
    <section class="border-t border-ink-800/60 bg-ink-900/30">
      <div class="mx-auto max-w-6xl px-4 py-14 sm:px-6">
        <h2 class="text-lg font-semibold tracking-tight">设计取舍</h2>
        <p class="mt-1.5 max-w-2xl text-sm text-mute-400">
          这套系统刻意放弃了一些看起来更"现代"的做法，因为它们在单机交易场景里只增加失败模式。
        </p>
        <div class="mt-8 grid gap-4 sm:grid-cols-2">
          <div v-for="f in features" :key="f.title" class="card card-pad">
            <div class="text-sm font-medium text-paper">{{ f.title }}</div>
            <p class="mt-2 text-xs leading-relaxed text-mute-400">{{ f.body }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- honest disclaimer -->
    <section class="mx-auto max-w-6xl px-4 py-14 sm:px-6">
      <div class="rounded-xl border border-warn-500/30 bg-warn-500/5 p-5">
        <div class="flex items-start gap-3">
          <svg viewBox="0 0 24 24" class="mt-0.5 h-5 w-5 shrink-0 text-warn-500" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <div>
            <div class="text-sm font-medium text-warn-500">风险声明</div>
            <p class="mt-1.5 text-xs leading-relaxed text-mute-300">
              本项目是技术演示与自用工具，不构成投资建议，也不承诺任何收益。
              默认使用纸面撮合；接入实盘需要同时满足配置开关与环境变量两道闸门。
              加密货币合约交易具有高杠杆风险，可能导致本金全部损失。
            </p>
          </div>
        </div>
      </div>
    </section>

    <footer class="border-t border-ink-800/60">
      <div class="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-6 text-xs text-mute-500 sm:px-6">
        <span>Origin Quant · v{{ metrics?.version ?? "—" }}</span>
        <span>Made for operators who read their own logs.</span>
      </div>
    </footer>
  </div>
</template>