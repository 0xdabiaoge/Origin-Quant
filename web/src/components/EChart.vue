<script setup lang="ts">
/** Minimal ECharts wrapper: one instance per mount, resized by ResizeObserver.
 *  ECharts is ~300kB, so it is only imported by views that actually chart. */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import * as echarts from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";

echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

const props = withDefaults(
  defineProps<{ option: EChartsOption; height?: string; loading?: boolean }>(),
  { height: "280px", loading: false },
);

const el = ref<HTMLDivElement | null>(null);
const chart = shallowRef<echarts.ECharts | null>(null);
let observer: ResizeObserver | null = null;

onMounted(() => {
  if (!el.value) return;
  chart.value = echarts.init(el.value, undefined, { renderer: "canvas" });
  chart.value.setOption(props.option);
  observer = new ResizeObserver(() => chart.value?.resize());
  observer.observe(el.value);
});

watch(
  () => props.option,
  (opt) => chart.value?.setOption(opt, { notMerge: true }),
  { deep: true },
);

watch(
  () => props.loading,
  (busy) => {
    if (!chart.value) return;
    if (busy) {
      chart.value.showLoading("default", {
        text: "",
        color: "#34d399",
        maskColor: "rgba(7,10,14,0.55)",
      });
    } else {
      chart.value.hideLoading();
    }
  },
);

onBeforeUnmount(() => {
  observer?.disconnect();
  chart.value?.dispose();
  chart.value = null;
});
</script>

<template>
  <div ref="el" :style="{ height, width: '100%' }" />
</template>
