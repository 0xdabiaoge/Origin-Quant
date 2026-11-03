<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "@/api/client";
import PageHeader from "@/components/PageHeader.vue";

type Settings = {
  enabled: boolean;
  poll_interval_s: number;
  credentials_available: boolean;
  event_types: string[];
  status: { last_result?: string; last_attempt_ms?: number; last_event_seq?: number; pending_event_seq?: number };
};
const config = ref<Settings | null>(null);
const busy = ref(false);
const error = ref("");
const notice = ref("");

async function load() {
  try {
    config.value = await api.get<Settings>("/api/v1/admin/notifications/config");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}

async function save() {
  if (!config.value) return;
  busy.value = true; error.value = ""; notice.value = "";
  try {
    config.value = await api.put<Settings>("/api/v1/admin/notifications/config", {
      enabled: config.value.enabled, poll_interval_s: Number(config.value.poll_interval_s),
    });
    notice.value = "通知配置已保存。";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败";
  } finally { busy.value = false; }
}

async function testDelivery() {
  busy.value = true; error.value = ""; notice.value = "";
  try {
    await api.post("/api/v1/admin/notifications/test");
    notice.value = "Telegram 已确认接收测试请求，请检查目标会话。";
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "测试失败";
  } finally { busy.value = false; }
}

onMounted(load);
</script>

<template>
  <div>
    <PageHeader title="风险通知" subtitle="可选的 Telegram 事件通知，默认关闭。" />
    <p v-if="error" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ error }}</p>
    <p v-if="notice" class="mb-4 rounded-lg border border-accent-600/40 bg-accent-600/10 px-3 py-2 text-sm text-accent-400">{{ notice }}</p>
    <form v-if="config" class="card card-pad space-y-4" @submit.prevent="save">
      <label class="flex items-center gap-2 text-sm"><input v-model="config.enabled" type="checkbox" />启用 Telegram 风险事件通知</label>
      <p class="text-xs leading-relaxed text-mute-400">服务器环境变量 OQ_NOTIFY_TELEGRAM_TOKEN 和 OQ_NOTIFY_TELEGRAM_CHAT_ID 分别配置机器人凭据和目标会话。页面不读取或显示其值。</p>
      <p class="text-xs text-mute-300">凭据状态：{{ config.credentials_available ? "已配置" : "未配置或无效" }}</p>
      <label class="block text-xs text-mute-400">检查间隔（秒）<input v-model.number="config.poll_interval_s" type="number" min="5" max="300" class="input mt-1 w-32" /></label>
      <p class="text-xs text-mute-400">事件范围：{{ config.event_types.join("、") || "未知" }}。仅发送事件类型、标的、时间和序号；投递可能延迟或重复。</p>
      <p class="text-xs text-mute-400">最近状态：{{ config.status.last_result ?? "暂无记录" }}<span v-if="config.status.last_attempt_ms"> · {{ new Date(config.status.last_attempt_ms).toLocaleString("zh-CN") }}</span></p>
      <div class="flex flex-wrap gap-2"><button type="submit" class="btn btn-primary" :disabled="busy">保存</button><button type="button" class="btn" :disabled="busy || !config.enabled" @click="testDelivery">发送测试通知</button></div>
    </form>
    <p v-else class="text-sm text-mute-400">正在读取通知配置…</p>
  </div>
</template>
