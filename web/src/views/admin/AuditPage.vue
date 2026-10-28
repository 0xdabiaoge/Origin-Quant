<script setup lang="ts">
import { onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import type { AuditRow } from "@/api/types";
import { fmtTime } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";

const rows = ref<AuditRow[]>([]);
const total = ref(0);
const offset = ref(0);
const limit = 50;
const action = ref("");
const err = ref("");

async function load() {
  try {
    const res = await v1.audit(limit, offset.value);
    rows.value = res.items;
    total.value = res.total;
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  }
}
onMounted(load);

function goto(delta: number) {
  offset.value = Math.max(0, offset.value + delta * limit);
  void load();
}
</script>

<template>
  <div>
    <PageHeader title="审计日志" subtitle="谁在什么时候做了什么。危险操作、用户变更、备份都会记录在这里。" />

    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div class="card">
      <div class="flex items-center justify-between border-b border-ink-800 px-4 py-3">
        <span class="label">共 {{ total }} 条</span>
        <div class="flex gap-1">
          <button class="btn btn-ghost px-2 py-1 text-xs" :disabled="offset === 0" @click="goto(-1)">上一页</button>
          <button class="btn btn-ghost px-2 py-1 text-xs" :disabled="offset + limit >= total" @click="goto(1)">下一页</button>
        </div>
      </div>
      <div v-if="rows.length" class="overflow-x-auto">
        <table class="table-base">
          <thead>
            <tr><th>时间</th><th>操作者</th><th>动作</th><th>详情</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.id">
              <td class="tnum whitespace-nowrap text-xs text-mute-400">{{ fmtTime(r.ts_ms) }}</td>
              <td class="text-xs">{{ r.actor }}</td>
              <td>
                <span class="chip bg-ink-700 text-mute-300">{{ r.action }}</span>
              </td>
              <td class="max-w-md truncate text-[11px] text-mute-500" :title="JSON.stringify(r.detail)">
                {{ r.detail ? JSON.stringify(r.detail) : '—' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else title="没有审计记录" hint="执行一次危险操作或用户变更后，这里就会有记录。" />
    </div>
  </div>
</template>
