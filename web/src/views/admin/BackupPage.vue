<script setup lang="ts">
import { onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import { fmtBytes, fmtTime } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";
import { useToast } from "@/components/useToast";

const items = ref<{ file: string; bytes: number; created_ms: number }[]>([]);
const diskFree = ref<number | undefined>();
const busy = ref(false);
const err = ref("");
const toast = useToast();

async function load() {
  try {
    const res = await v1.backups();
    items.value = res.items;
    diskFree.value = res.disk_free;
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
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

    <p class="mt-3 text-[11px] leading-relaxed text-mute-500">
      备份写在 <code class="tnum">backups/</code> 目录。恢复需要停容器后用备份文件替换数据库，这一步刻意没有做成按钮。
    </p>
  </div>
</template>
