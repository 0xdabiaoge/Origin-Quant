<script setup lang="ts">
import { ref } from "vue";
import { v1 } from "@/api/client";
import PageHeader from "@/components/PageHeader.vue";
import { useToast } from "@/components/useToast";

interface Action {
  key: string;
  title: string;
  body: string;
  confirm: string;
  tone: "danger" | "warn" | "neutral";
}

const actions: Action[] = [
  {
    key: "kill",
    title: "熔断（kill switch）",
    body: "立即停止一切新开仓。已有持仓保留，需要手动处理。这是最温和的紧急停止。",
    confirm: "KILL",
    tone: "warn",
  },
  {
    key: "pause",
    title: "暂停引擎",
    body: "暂停调度器，不再自动跑周期。不会平仓，也不会拒绝已有订单。",
    confirm: "PAUSE",
    tone: "neutral",
  },
  {
    key: "resume",
    title: "恢复引擎",
    body: "解除暂停与熔断，恢复正常调度。",
    confirm: "RESUME",
    tone: "neutral",
  },
  {
    key: "flatten",
    title: "全部平仓",
    body: "以市价平掉所有持仓并触发熔断。这是不可逆操作：平仓后无法撤销，滑点真实发生。",
    confirm: "FLATTEN",
    tone: "danger",
  },
];

const toast = useToast();
const armed = ref<string | null>(null);
const typed = ref("");
const busy = ref<string | null>(null);

async function run(a: Action) {
  busy.value = a.key;
  try {
    await v1.danger(a.key, typed.value);
    toast.ok(`${a.title} 已执行`);
    armed.value = null;
    typed.value = "";
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "执行失败");
  } finally {
    busy.value = null;
  }
}
</script>

<template>
  <div>
    <PageHeader title="危险操作" subtitle="这些操作会改变引擎的运行状态，全部写入审计日志。" />

    <div class="mb-4 rounded-lg border border-danger-600/30 bg-danger-600/5 px-4 py-3 text-xs leading-relaxed text-mute-300">
      <span class="font-medium text-danger-500">这个页面的按钮没有撤销。</span>
      每个操作都要求手动输入确认词，是为了让"手滑"变成一件需要费力的事。
    </div>

    <div class="grid gap-3 lg:grid-cols-2">
      <div v-for="a in actions" :key="a.key" class="card card-pad">
        <div class="flex items-start justify-between gap-3">
          <div>
            <div class="text-sm font-medium">{{ a.title }}</div>
            <p class="mt-1.5 text-xs leading-relaxed text-mute-400">{{ a.body }}</p>
          </div>
          <span
            class="chip shrink-0"
            :class="{
              'bg-danger-600/10 text-danger-500': a.tone === 'danger',
              'bg-warn-500/10 text-warn-500': a.tone === 'warn',
              'bg-ink-700 text-mute-300': a.tone === 'neutral',
            }"
          >
            {{ a.tone }}
          </span>
        </div>

        <div v-if="armed === a.key" class="mt-4 space-y-2">
          <label class="text-[11px] text-mute-400">
            输入 <code class="tnum font-semibold text-paper">{{ a.confirm }}</code> 以确认
          </label>
          <input
            v-model="typed"
            class="input text-xs"
            :placeholder="a.confirm"
            spellcheck="false"
            autocomplete="off"
          />
          <div class="flex gap-2">
            <button
              class="btn text-xs"
              :class="a.tone === 'danger' ? 'btn-danger' : 'btn-primary'"
              :disabled="busy === a.key || typed.trim().toUpperCase() !== a.confirm"
              @click="run(a)"
            >
              {{ busy === a.key ? "执行中…" : "确认执行" }}
            </button>
            <button class="btn btn-ghost text-xs" @click="armed = null; typed = ''">取消</button>
          </div>
        </div>
        <button
          v-else
          class="btn mt-4 w-full text-xs"
          :class="a.tone === 'danger' ? 'btn-danger' : ''"
          @click="armed = a.key; typed = ''"
        >
          执行
        </button>
      </div>
    </div>
  </div>
</template>
