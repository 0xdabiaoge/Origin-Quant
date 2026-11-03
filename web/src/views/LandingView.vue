<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { v1 } from "@/api/client";
import type { PublicMetrics } from "@/api/types";

const status = ref<PublicMetrics | null>(null);
const statusError = ref(false);
onMounted(async () => {
  try { status.value = await v1.metrics(); }
  catch { statusError.value = true; }
});
function lastCycle(ms: number | null | undefined): string {
  return ms ? new Date(ms).toLocaleString("zh-CN", { hour12: false }) : "暂无周期记录";
}
function modeLabel(mode: string | undefined): string {
  return ({ paper: "纸面模拟", live: "实盘模式", backtest: "回测模式" } as Record<string, string>)[mode ?? ""] ?? "未知";
}

const features = [
  { title: "市场与策略", body: "使用行情、资金费和量化策略构建交易意图，交易决策可回溯。" },
  { title: "AI 辅助分析", body: "结合可配置的模型和新闻信息提供研究意见，分析结果与执行状态分开记录。" },
  { title: "统一风险管理", body: "交易意图经过风险规则、数量计算与订单管理，执行结果写入账本。" },
  { title: "运行与复盘", body: "在工作台查看持仓、订单、风险决策与历史记录，管理后台提供配置及审计。" },
];
</script>

<template>
  <div class="min-h-screen bg-ink-950">
    <header class="sticky top-0 z-30 border-b border-ink-800/80 bg-ink-950/80 backdrop-blur">
      <div class="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
        <div class="flex items-center gap-2.5">
          <svg viewBox="0 0 32 32" class="h-7 w-7" aria-hidden="true"><rect width="32" height="32" rx="8" fill="#0f141b" /><path d="M8 22 L14 12 L19 17 L24 9" stroke="#34d399" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" /></svg>
          <span class="text-sm font-semibold tracking-tight">Origin Quant</span>
        </div>
        <nav class="flex items-center gap-1 text-sm"><RouterLink to="/docs" class="btn btn-ghost text-mute-300">文档</RouterLink><RouterLink to="/login" class="btn btn-primary">进入工作台</RouterLink></nav>
      </div>
    </header>
    <section class="mx-auto max-w-6xl px-4 pb-16 pt-14 sm:px-6 sm:pt-20">
      <div class="grid items-center gap-10 lg:grid-cols-[1.1fr_1fr]">
        <div>
          <div class="chip mb-5 border border-accent-600/40 bg-accent-600/10 text-accent-400">市场数据 · AI 分析 · 风险管理</div>
          <h1 class="text-3xl font-semibold leading-tight tracking-tight sm:text-4xl lg:text-5xl">让每一次决策<br /><span class="text-accent-400">都有据可循</span></h1>
          <p class="mt-5 max-w-xl text-sm leading-relaxed text-mute-300 sm:text-base">Origin Quant 将市场数据、量化策略与 AI 研究结合，在统一的风险与执行流程中记录决策，帮助操作者观察、分析和复盘。</p>
          <div class="mt-8 flex flex-wrap gap-3"><RouterLink to="/login" class="btn btn-primary px-5 py-2.5">打开工作台</RouterLink><RouterLink to="/docs" class="btn px-5 py-2.5">了解系统</RouterLink></div>
        </div>
        <div class="card card-pad">
          <div class="label">交易工作流</div>
          <div class="mt-5 space-y-3">
            <div v-for="(step, i) in ['采集市场与新闻信息', '生成策略和 AI 分析', '执行风险检查', '记录执行与复盘']" :key="step" class="flex items-center gap-3 rounded-lg border border-ink-800 bg-ink-950/50 p-3">
              <span class="tnum flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-600/10 text-xs text-accent-400">0{{ i + 1 }}</span><span class="text-sm text-mute-300">{{ step }}</span>
            </div>
          </div>
          <div class="mt-5 border-t border-ink-800 pt-4">
            <div class="flex items-center justify-between gap-3"><span class="label">服务返回的状态</span><span class="text-[11px] text-mute-500">{{ statusError ? '暂不可读取' : status ? '本次页面加载' : '读取中…' }}</span></div>
            <dl class="mt-3 grid gap-3 text-xs sm:grid-cols-3">
              <div><dt class="text-mute-500">运行模式</dt><dd class="mt-1 text-paper">{{ modeLabel(status?.mode) }}</dd></div>
              <div><dt class="text-mute-500">服务版本</dt><dd class="tnum mt-1 text-paper">{{ status?.version ?? '—' }}</dd></div>
              <div><dt class="text-mute-500">最近周期</dt><dd class="mt-1 text-paper">{{ status ? lastCycle(status.last_cycle_ms) : '未知' }}</dd></div>
            </dl>
          </div>
          <p class="mt-4 text-[11px] leading-relaxed text-mute-500">周期时间不代表成交或盈利。公开页面不展示账户余额、持仓或订单。</p>
        </div>
      </div>
    </section>
    <section class="border-t border-ink-800/60 bg-ink-900/30"><div class="mx-auto max-w-6xl px-4 py-14 sm:px-6">
      <h2 class="text-lg font-semibold tracking-tight">从信息到决策</h2>
      <p class="mt-1.5 max-w-2xl text-sm text-mute-400">围绕市场信息、策略研究与运行记录，构建可检查的工作流。</p>
      <div class="mt-8 grid gap-4 sm:grid-cols-2"><div v-for="f in features" :key="f.title" class="card card-pad"><div class="text-sm font-medium text-paper">{{ f.title }}</div><p class="mt-2 text-xs leading-relaxed text-mute-400">{{ f.body }}</p></div></div>
    </div></section>
    <section class="mx-auto max-w-6xl px-4 py-12 sm:px-6"><div class="rounded-xl border border-warn-500/30 bg-warn-500/5 p-5"><div class="text-sm font-medium text-warn-500">风险说明</div><p class="mt-1.5 text-xs leading-relaxed text-mute-300">系统用于研究与自用，不构成投资建议或收益承诺。模拟结果与真实交易存在差异，加密资产交易可能造成资金损失。</p></div></section>
    <footer class="border-t border-ink-800/60"><div class="mx-auto max-w-6xl px-4 py-6 text-xs text-mute-500 sm:px-6">Origin Quant · 市场研究与交易工作台</div></footer>
  </div>
</template>
