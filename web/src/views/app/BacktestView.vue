<script setup lang="ts">
import { ref } from "vue";
import { v1 } from "@/api/client";
import type { BacktestResult } from "@/api/types";
import PageHeader from "@/components/PageHeader.vue";
import { useToast } from "@/components/useToast";

const days = ref(90);
const source = ref<"synthetic" | "parquet">("synthetic");
const running = ref(false);
const result = ref<BacktestResult | null>(null);
const err = ref("");
const toast = useToast();

function copyReport() {
  if (result.value?.report) void navigator.clipboard.writeText(result.value.report);
}

async function run() {
  running.value = true;
  err.value = "";
  result.value = null;
  try {
    result.value = await v1.backtest({ days: days.value, source: source.value });
    if (result.value.error) err.value = result.value.error;
    else toast.ok("回测完成");
  } catch (e) {
    err.value = e instanceof Error ? e.message : "回测失败";
  } finally {
    running.value = false;
  }
}
</script>

<template>
  <div>
    <PageHeader title="回测" subtitle="在历史数据上重放策略。合成数据适合验证逻辑，不适合评估收益。" />

    <div class="card card-pad">
      <div class="flex flex-wrap items-end gap-3">
        <div class="w-32">
          <label class="label mb-1.5 block" for="days">天数</label>
          <input id="days" v-model.number="days" type="number" min="5" max="365" class="input" />
        </div>
        <div class="w-40">
          <label class="label mb-1.5 block" for="src">数据源</label>
          <select id="src" v-model="source" class="input">
            <option value="synthetic">合成数据</option>
            <option value="parquet">本地 Parquet</option>
          </select>
        </div>
        <button class="btn btn-primary" :disabled="running" @click="run">
          {{ running ? "回测中…" : "开始回测" }}
        </button>
      </div>
      <p class="mt-3 text-[11px] leading-relaxed text-mute-500">
        回测走的是与实盘完全相同的策略内核和风控链路，差异只在撮合器。
      </p>
    </div>

    <p v-if="err" class="mt-3 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div v-if="result?.report" class="card mt-3">
      <div class="flex items-center justify-between border-b border-ink-800 px-4 py-3">
        <span class="label">回测报告</span>
        <button class="btn btn-ghost text-xs" @click="copyReport">复制</button>
      </div>
      <pre class="overflow-x-auto p-4 text-[12px] leading-relaxed text-mute-300"><code class="tnum">{{ result.report }}</code></pre>
    </div>

    <div v-else-if="!running" class="card mt-3">
      <div class="flex flex-col items-center justify-center gap-2 px-4 py-14 text-center">
        <div class="text-sm text-mute-300">还没有回测结果</div>
        <div class="max-w-md text-xs leading-relaxed text-mute-500">
          点击"开始回测"在合成数据上重放。历史结果：90 天合成数据净值为负，成本吃掉了全部边际。
        </div>
      </div>
    </div>
  </div>
</template>
