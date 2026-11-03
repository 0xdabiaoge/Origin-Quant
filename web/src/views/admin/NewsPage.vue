<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import type { NewsItem } from "@/api/types";
import PageHeader from "@/components/PageHeader.vue";

const items = ref<NewsItem[]>([]);
const fetched = ref<number | null>(null);
const errors = ref<Record<string, string>>({});
const error = ref("");
const busy = ref(false);
const loading = ref(true);
const query = ref("");
const shown = computed(() => {
  const value = query.value.trim().toLowerCase();
  return value ? items.value.filter((item) => `${item.title} ${item.summary} ${item.source}`.toLowerCase().includes(value)) : items.value;
});

async function load() {
  error.value = "";
  try {
    const data = await v1.news();
    items.value = data.items; fetched.value = data.fetched_ms; errors.value = data.errors;
  } catch (e) { error.value = e instanceof Error ? e.message : "加载失败"; }
  finally { loading.value = false; }
}
async function refresh() {
  busy.value = true; error.value = "";
  try { await v1.refreshNews(); await load(); }
  catch (e) { error.value = e instanceof Error ? e.message : "刷新失败"; }
  finally { busy.value = false; }
}
function time(ms: number | null | undefined): string { return ms ? new Date(ms).toLocaleString("zh-CN", { hour12: false }) : "未知"; }
function ageState(ms: number | null | undefined): string {
  if (!ms || ms > Date.now()) return "发布时间未知";
  return Date.now() - ms > 86_400_000 ? "超过 24 小时" : "24 小时内";
}
function safeUrl(url: string): string | null {
  try { const parsed = new URL(url); return parsed.protocol === "https:" ? parsed.href : null; } catch { return null; }
}
onMounted(load);
</script>

<template>
  <div>
    <PageHeader title="新闻情报" subtitle="采集来源、发布时间和采集时间分开展示。">
      <template #actions><button class="btn text-xs" :disabled="busy" @click="refresh">{{ busy ? '采集中…' : '刷新新闻' }}</button></template>
    </PageHeader>
    <p v-if="error" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ error }}</p>
    <div v-if="Object.keys(errors).length" class="mb-4 rounded-lg border border-warn-500/30 bg-warn-500/5 px-3 py-2 text-xs text-warn-500">采集异常：<span v-for="(reason, source) in errors" :key="source" class="mr-2">{{ source }}：{{ reason }}</span></div>
    <div class="mb-3 flex flex-wrap items-center justify-between gap-3"><div class="text-xs text-mute-500">最近采集：{{ time(fetched) }}。新闻来自外部来源，其内容和影响需要独立核实。</div><label class="w-full sm:w-64"><span class="sr-only">搜索新闻</span><input v-model="query" class="input text-xs" placeholder="按标题、摘要或来源搜索" /></label></div>
    <div class="card">
      <div v-if="loading" class="p-5 text-sm text-mute-500">加载中…</div>
      <div v-else-if="!items.length" class="p-5 text-sm text-mute-500">暂无新闻。点击刷新新闻可从配置的来源采集。</div>
      <div v-else-if="!shown.length" class="p-5 text-sm text-mute-500">没有匹配的新闻。</div>
      <article v-for="item in shown" :key="item.id" class="border-b border-ink-800 p-4 last:border-0">
        <a v-if="safeUrl(item.url)" :href="safeUrl(item.url)!" target="_blank" rel="noopener noreferrer" class="text-sm font-medium text-accent-400 hover:underline">{{ item.title }}</a>
        <div v-else class="text-sm font-medium">{{ item.title }}</div>
        <p v-if="item.summary" class="mt-2 text-xs leading-relaxed text-mute-300">{{ item.summary }}</p>
        <div class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-mute-500"><span>{{ item.source }} · 发布 {{ time(item.published_ms) }} · 采集 {{ time(item.fetched_ms) }}</span><span class="chip bg-ink-800 text-mute-300">{{ ageState(item.published_ms) }}</span></div>
      </article>
    </div>
  </div>
</template>
