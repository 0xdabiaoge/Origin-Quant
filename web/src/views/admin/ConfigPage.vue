<script setup lang="ts">
import { onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import PageHeader from "@/components/PageHeader.vue";

const config = ref<unknown>(null);
const hash = ref("");
const paths = ref<Record<string, string>>({});
const err = ref("");
const loading = ref(true);

onMounted(async () => {
  try {
    const res = await v1.config();
    config.value = res.config;
    hash.value = res.hash;
    paths.value = res.paths;
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div>
    <PageHeader title="配置" subtitle="当前生效的配置快照。敏感字段已脱敏。">
      <template #actions>
        <span class="tnum text-[11px] text-mute-500">指纹 {{ hash.slice(0, 12) }}</span>
      </template>
    </PageHeader>

    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div class="card card-pad mb-3">
      <div class="label mb-3">路径</div>
      <dl class="space-y-1.5 text-sm">
        <div v-for="(v, k) in paths" :key="k" class="flex items-baseline justify-between gap-4">
          <dt class="text-mute-400">{{ k }}</dt>
          <dd class="tnum truncate text-xs text-mute-300">{{ v }}</dd>
        </div>
      </dl>
    </div>

    <div class="card">
      <div class="border-b border-ink-800 px-4 py-3">
        <span class="label">完整配置</span>
      </div>
      <pre class="max-h-[560px] overflow-auto p-4 text-[12px] leading-relaxed text-mute-300"><code class="tnum">{{ loading ? '加载中…' : JSON.stringify(config, null, 2) }}</code></pre>
    </div>

    <p class="mt-3 text-[11px] leading-relaxed text-mute-500">
      交易引擎配置通过 config/*.yaml 文件管理，当前页面仅供查看；配置变更需要重新加载或重启进程。
    </p>
  </div>
</template>
