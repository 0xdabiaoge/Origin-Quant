<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, v1 } from "@/api/client";
import { fmtBytes, fmtTime } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";
import { useToast } from "@/components/useToast";

const items = ref<{ file: string; bytes: number; created_ms: number }[]>([]);
const fullItems = ref<{ file: string; bytes: number; created_ms: number }[]>([]);
const diskFree = ref<number | undefined>();
const busy = ref(false);
const err = ref("");
const toast = useToast();

async function load() {
  try {
    const res = await v1.backups();
    items.value = res.items;
    diskFree.value = res.disk_free;
    fullItems.value = (await api.get<{items: typeof fullItems.value}>("/api/v1/admin/backups/full")).items;
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  }
}

async function createFull() {
  busy.value = true;
  try {
    const res = await api.post<{file: string; bytes: number}>("/api/v1/admin/backups/full");
    toast.ok(`已创建 ${res.file}（${fmtBytes(res.bytes)}）`);
    await load();
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "备份失败");
  } finally {
    busy.value = false;
  }
}

async function verify(file: string) {
  busy.value = true;
  try {
    await api.post(`/api/v1/admin/backups/full/${encodeURIComponent(file)}/verify`);
    toast.ok("备份内容校验通过；仍需单独执行隔离恢复验证");
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "校验失败");
  } finally {
    busy.value = false;
  }
}
onMounted(load);

async function create() {
  busy.value = true;
  try {
    const res = await v1.backup();
    toast.ok(`已备份 ${res.file}（${fmtBytes(res.bytes)}）`);
    await load();
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "备份失败");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div>
    <PageHeader title="备份" :subtitle="diskFree ? `磁盘剩余 ${fmtBytes(diskFree)}` : '数据库快照'">
      <template #actions>
        <button class="btn btn-primary" :disabled="busy" @click="create">{{ busy ? "备份中…" : "立即备份" }}</button>
        <button class="btn btn-primary ml-2" :disabled="busy" @click="createFull">创建完整归档</button>
      </template>
    </PageHeader>

    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div class="card">
      <div v-if="items.length" class="overflow-x-auto">
        <table class="table-base">
          <thead>
            <tr><th>文件</th><th class="text-right">大小</th><th>创建时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="b in items" :key="b.file">
              <td class="tnum text-xs">{{ b.file }}</td>
              <td class="tnum text-right text-xs text-mute-300">{{ fmtBytes(b.bytes) }}</td>
              <td class="tnum text-xs text-mute-400">{{ fmtTime(b.created_ms) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else title="还没有备份" hint="备份使用 SQLite 在线备份 API，引擎运行时也能安全执行。" />
    </div>

    <div class="card mt-4 card-pad">
      <div class="label mb-3">完整归档</div>
      <div v-if="fullItems.length" class="space-y-3">
        <div v-for="b in fullItems" :key="b.file" class="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span class="tnum">{{ b.file }} · {{ fmtBytes(b.bytes) }} · {{ fmtTime(b.created_ms) }}</span>
          <button class="btn btn-ghost" :disabled="busy" @click="verify(b.file)">校验</button>
        </div>
      </div>
      <EmptyState v-else title="还没有完整归档" hint="包含数据库快照、行情 Parquet 和配置 YAML。" />
    </div>

    <p class="mt-3 text-[11px] leading-relaxed text-mute-500">
      归档包含数据库快照、行情文件和配置；不包含环境文件或独立凭据文件。配置与数据库仍可能含敏感数据，请妥善保管。
      数据库与行情文件分别复制，并非跨文件同一时刻快照。校验只检查归档完整性，不能替代隔离恢复验证。
    </p>
  </div>
</template>
