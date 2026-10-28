<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";

const lines = ref<string[]>([]);
const path = ref<string | null>(null);
const available = ref(true);
const err = ref("");
const filter = ref("");

async function load() {
  try {
    const res = await v1.logs(400);
    lines.value = res.lines;
    path.value = res.path;
    available.value = res.available;
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  }
}
onMounted(load);

const shown = computed(() => {
  const q = filter.value.trim().toLowerCase();
  return q ? lines.value.filter((l) => l.toLowerCase().includes(q)) : lines.value;
});

function levelOf(line: string): string {
  if (/\bERROR\b|\bCRITICAL\b/.test(line)) return "text-danger-500";
  if (/\bWARNING\b|\bWARN\b/.test(line)) return "text-warn-500";
  return "text-mute-400";
}
</script>

<template>
  <div>
    <PageHeader title="运行日志" :subtitle="path ?? '未找到日志文件'">
      <template #actions>
        <input v-model="filter" class="input w-44 py-1.5 text-xs" placeholder="过滤…" />
        <button class="btn text-xs" @click="load">刷新</button>
      </template>
    </PageHeader>

    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div class="card">
      <div v-if="shown.length" class="max-h-[640px] overflow-auto p-4">
        <pre class="text-[11.5px] leading-relaxed"><code
          v-for="(l, i) in shown"
          :key="i"
          class="block whitespace-pre-wrap break-all"
          :class="levelOf(l)"
        >{{ l }}</code></pre>
      </div>
      <EmptyState
        v-else
        :title="available ? '日志为空' : '没有找到日志文件'"
        :hint="available ? '引擎还没有写出任何日志。' : '日志默认写到 <项目根>/logs/*.log。容器部署时该目录可能没有挂载。'"
      />
    </div>
  </div>
</template>
