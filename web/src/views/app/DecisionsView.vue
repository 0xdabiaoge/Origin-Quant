<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import type { DecisionsPayload } from "@/api/types";
import { fmtTime } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";

const data = ref<DecisionsPayload | null>(null);
const err = ref("");
const onlyRejected = ref(false);

onMounted(async () => {
  try {
    data.value = await v1.decisions(200);
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  }
});

const rows = computed(() => {
  const all = data.value?.items ?? [];
  return onlyRejected.value ? all.filter((d) => !d.approved) : all;
});

const rejections = computed(() => data.value?.rejections ?? []);
const maxReject = computed(() => Math.max(1, ...rejections.value.map((r) => r.n)));

function gatesText(raw: string): string {
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return "";
    const failed = parsed.filter((g: { ok?: boolean }) => g.ok === false);
    return failed.map((g: { code?: string; detail?: string }) => g.code ?? g.detail ?? "?").join(", ");
  } catch {
    return "";
  }
}
</script>

<template>
  <div>
    <PageHeader title="决策流水" subtitle="策略的每一次判断，包含被风控拒绝的及其原因。">
      <template #actions>
        <label class="flex cursor-pointer items-center gap-2 text-xs text-mute-300">
          <input v-model="onlyRejected" type="checkbox" class="accent-accent-500" />
          只看被拒绝
        </label>
      </template>
    </PageHeader>

    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div v-if="rejections.length" class="card card-pad mb-3">
      <div class="label mb-3">拒绝原因分布</div>
      <div class="space-y-2">
        <div v-for="r in rejections" :key="r.gate" class="flex items-center gap-3">
          <span class="tnum w-32 shrink-0 truncate text-xs text-mute-300">{{ r.gate || "(未标注)" }}</span>
          <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-800">
            <div class="h-full rounded-full bg-danger-500/70" :style="{ width: `${(r.n / maxReject) * 100}%` }" />
          </div>
          <span class="tnum w-10 shrink-0 text-right text-xs text-mute-400">{{ r.n }}</span>
        </div>
      </div>
    </div>

    <div class="card">
      <div v-if="rows.length" class="overflow-x-auto">
        <table class="table-base">
          <thead>
            <tr><th>时间</th><th>策略</th><th>账本</th><th>动作</th><th>标的</th><th>结果</th><th>未通过的闸门</th></tr>
          </thead>
          <tbody>
            <tr v-for="d in rows" :key="d.id">
              <td class="tnum whitespace-nowrap text-xs text-mute-400">{{ fmtTime(d.ts_ms) }}</td>
              <td class="text-xs">{{ d.strategy_id }}</td>
              <td>
                <span class="chip" :class="d.book === 'carry' ? 'bg-info-500/10 text-info-500' : 'bg-warn-500/10 text-warn-500'">{{ d.book }}</span>
              </td>
              <td class="text-xs text-mute-300">{{ d.action }}</td>
              <td class="tnum text-xs text-mute-400">{{ d.symbols }}</td>
              <td>
                <span class="chip" :class="d.approved ? 'bg-accent-600/10 text-accent-400' : 'bg-danger-600/10 text-danger-500'">
                  {{ d.approved ? "通过" : "拒绝" }}
                </span>
              </td>
              <td class="text-[11px] text-mute-500">{{ gatesText(d.gates) || "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else title="没有决策记录" hint="跑一个周期后，策略的每次判断都会记录在这里。" />
    </div>
  </div>
</template>
