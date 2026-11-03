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
    body: "触发熔断，阻止后续新增风险。已有持仓保留，在途订单仍可能成交。",
    confirm: "KILL",
    tone: "warn",
  },
  {
    key: "pause",
    title: "暂停引擎",
    body: "暂停新增交易风险；调度器可能继续运行，用于记录周期状态。不会自动平仓或撤销已有订单。",
    confirm: "PAUSE",
    tone: "neutral",
  },
  {
    key: "resume",
    title: "恢复引擎",
    body: "请求解除交易暂停。熔断状态及是否允许开仓请以系统实际状态为准。",
    confirm: "RESUME",
    tone: "neutral",
  },
  {
    key: "reset_kill",
    title: "解除熔断",
    body: "单独解除熔断标志；请先核对交易所持仓、保护单和异常。恢复暂停不会自动解除熔断。",
    confirm: "RESET_KILL",
    tone: "warn",
  },
  {
    key: "flatten",
    title: "全部平仓",
    body: "触发熔断并请求逐仓位平仓。在途订单仍可能成交；本地处理结果不能证明交易所仓位已归零。",
    confirm: "FLATTEN",
    tone: "danger",
  },
];

const toast = useToast();
const armed = ref<string | null>(null);
const typed = ref("");
const busy = ref<string | null>(null);
const flattenResults = ref<{ symbol: string; book: string; status: string; errors: string[]; remaining_local_qty?: string }[] | null>(null);
const flattenError = ref("");
const flattenUnreported = ref(false);

async function run(a: Action) {
  if (busy.value) return;
  busy.value = a.key;
  if (a.key === "flatten") {
    flattenResults.value = null;
    flattenError.value = "";
    flattenUnreported.value = false;
  }
  try {
    const result = await v1.danger(a.key, typed.value);
    if (a.key === "flatten") {
      flattenResults.value = Array.isArray(result.outcomes) ? result.outcomes : null;
      flattenUnreported.value = flattenResults.value === null;
      toast.info("平仓请求已返回，请核对本地结果及交易所持仓。");
    } else toast.ok(`${a.title}请求已提交，请检查引擎及交易所状态确认结果`);
    armed.value = null;
    typed.value = "";
  } catch (e) {
    const message = e instanceof Error ? e.message : "请求未成功，请核对实际状态";
    if (a.key === "flatten") flattenError.value = message;
    else toast.error(message);
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

    <div v-if="flattenError" role="alert" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-4 py-3 text-sm text-danger-500">
      <div class="font-semibold">平仓请求未完成</div>
      <p class="mt-1 whitespace-pre-wrap break-words">{{ flattenError }}</p>
      <p class="mt-1 text-xs text-mute-300">请核对引擎与交易所的实际状态，不能仅凭此响应认定仓位已平。</p>
    </div>
    <div v-if="flattenUnreported" role="alert" class="mb-4 rounded-lg border border-warn-500/30 bg-warn-500/5 px-4 py-3 text-sm text-warn-500">响应未包含逐仓位处理结果，无法确认平仓结果，请核对交易所持仓。</div>

    <section v-if="flattenResults !== null" class="card card-pad mb-4">
      <h2 class="text-sm font-semibold">平仓请求的本地处理结果</h2>
      <p class="mt-1 text-xs text-warn-500">本地记录不能证明交易所仓位已归零，请在交易所独立核对。</p>
      <p v-if="!flattenResults.length" class="mt-2 text-xs text-mute-400">本地没有待处理的持仓记录。</p>
      <div v-for="row in flattenResults" :key="`${row.symbol}-${row.book}`" class="mt-2 text-xs text-mute-300">
        {{ row.symbol }} / {{ row.book }}：{{ row.status }}<span v-if="row.remaining_local_qty !== undefined"> · 本地剩余 {{ row.remaining_local_qty }}</span><span v-if="row.errors.length" class="text-danger-500"> · {{ row.errors.join('；') }}</span>
      </div>
    </section>

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
              :disabled="!!busy || typed.trim().toUpperCase() !== a.confirm"
              @click="run(a)"
            >
              {{ busy === a.key ? "执行中…" : "确认执行" }}
            </button>
            <button class="btn btn-ghost text-xs" :disabled="!!busy" @click="armed = null; typed = ''">取消</button>
          </div>
        </div>
        <button
          v-else
          class="btn mt-4 w-full text-xs"
          :class="a.tone === 'danger' ? 'btn-danger' : ''"
          :disabled="!!busy"
          @click="armed = a.key; typed = ''"
        >
          执行
        </button>
      </div>
    </div>
  </div>
</template>
