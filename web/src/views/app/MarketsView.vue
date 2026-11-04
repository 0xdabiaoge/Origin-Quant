<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import MarketTerminal from "@/components/MarketTerminal.vue";
import { v1 } from "@/api/client";
import type { Position } from "@/api/types";
const positions = ref<Position[]>([]);
const error = ref("");
let timer: number | undefined;
let disposed = false;
async function refreshPositions() {
  try {
    const result = await v1.positions();
    if (disposed) return;
    positions.value = result.positions;
    error.value = "";
  } catch {
    if (disposed) return;
    positions.value = [];
    error.value = "本地持仓参考线暂不可用，请到持仓页核对。";
  }
}
onMounted(() => { void refreshPositions(); timer = window.setInterval(() => void refreshPositions(), 30000); });
onUnmounted(() => { disposed = true; window.clearInterval(timer); });
</script>
<template>
  <div><PageHeader title="行情终端" subtitle="从市场走势到策略判断，在同一个工作台中观察。" /><p v-if="error" class="mb-3 text-xs text-warn-500">{{ error }}</p><MarketTerminal :positions="positions" /></div>
</template>
