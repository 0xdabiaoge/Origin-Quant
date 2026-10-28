<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import type { DataPayload } from "@/api/types";
import { fmtAgo, fmtTime } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";
import StatCard from "@/components/StatCard.vue";
import EmptyState from "@/components/EmptyState.vue";

const data = ref<DataPayload | null>(null);
const err = ref("");

onMounted(async () => {
  try {
    data.value = await v1.data();
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  }
});

const rows = computed(() => data.value?.instruments ?? []);
const stale = computed(() => rows.value.filter((r) => r.stale).length);
const fresh = computed(() => rows.value.length - stale.value);
</script>

<template>
  <div>
    <PageHeader title="数据新鲜度" :subtitle="`时间周期 ${data?.timeframe ?? '—'} · 过期的标的不会参与交易`" />

    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div class="grid gap-3 sm:grid-cols-3">
      <StatCard label="标的数量" :value="String(rows.length)" />
      <StatCard label="新鲜" :value="String(fresh)" tone="up" />
      <StatCard label="过期" :value="String(stale)" :tone="stale > 0 ? 'warn' : 'neutral'" hint="落后超过一个周期" />
    </div>

    <div v-if="stale === rows.length && rows.length > 0" class="mt-3 rounded-lg border border-warn-500/30 bg-warn-500/5 px-4 py-3 text-xs leading-relaxed text-mute-300">
      <span class="font-medium text-warn-500">全部标的都已过期。</span>
      调度器会持续跳过周期（skipped: no_data）。这是预期行为——合成数据不会自己增长。
      要恢复交易，需要接入真实行情源并重新灌入数据。
    </div>

    <div class="card mt-3">
      <div v-if="rows.length" class="overflow-x-auto">
        <table class="table-base">
          <thead>
            <tr><th>标的</th><th>最新 K 线</th><th class="text-right">距今</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.symbol">
              <td class="tnum text-xs">{{ r.symbol }}</td>
              <td class="tnum text-xs text-mute-300">{{ r.last_bar_ms ? fmtTime(r.last_bar_ms) : '无数据' }}</td>
              <td class="tnum text-right text-xs" :class="r.stale ? 'text-warn-500' : 'text-mute-300'">
                {{ r.age_min === null ? '—' : `${r.age_min} 分钟` }}
              </td>
              <td>
                <span class="chip" :class="r.stale ? 'bg-warn-500/10 text-warn-500' : 'bg-accent-600/10 text-accent-400'">
                  {{ r.stale ? '过期' : '新鲜' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else title="没有标的配置" hint="检查 config/instruments.toml。" />
    </div>
  </div>
</template>
