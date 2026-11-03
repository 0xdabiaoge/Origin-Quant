<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { v1 } from "@/api/client";
import type { AiCouncilSeat, AiDecision } from "@/api/types";
import PageHeader from "@/components/PageHeader.vue";

const endpoints: Record<string, string> = { openai: "https://api.openai.com/v1/chat/completions", deepseek: "https://api.deepseek.com/chat/completions", anthropic: "https://api.anthropic.com/v1/messages" };
const form = reactive<{ enabled: boolean; provider: string; model: string; endpoint: string; api_key_env: string; timeout_s: number; auto_paper_enabled: boolean; paper_confirmation_enabled: boolean; auto_symbol: string; auto_news_enabled: boolean; news_interval_s: number; council_enabled: boolean; council_seats: AiCouncilSeat[] }>({ enabled: false, provider: "openai", model: "", endpoint: endpoints.openai!, api_key_env: "OQ_AI_KEY_PRIMARY", timeout_s: 30, auto_paper_enabled: false, paper_confirmation_enabled: false, auto_symbol: "BTC-USDT-SWAP", auto_news_enabled: false, news_interval_s: 1800, council_enabled: false, council_seats: [] });
const hasKey = ref(false);
const history = ref<AiDecision[]>([]);
const symbol = ref("BTC-USDT-SWAP");
const loading = ref(true);
const busy = ref("");
const error = ref("");
const notice = ref("");

async function load() {
  error.value = "";
  try {
    const [config, records] = await Promise.all([v1.aiConfig(), v1.aiHistory()]);
    Object.assign(form, {
      enabled: config.enabled, provider: config.provider, model: config.model,
      endpoint: config.endpoint, api_key_env: config.api_key_env, timeout_s: config.timeout_s,
      auto_paper_enabled: config.auto_paper_enabled,
      paper_confirmation_enabled: config.paper_confirmation_enabled ?? false,
      auto_symbol: config.auto_symbol,
      auto_news_enabled: config.auto_news_enabled, news_interval_s: config.news_interval_s,
      council_enabled: config.council_enabled ?? false,
      council_seats: (config.council_seats ?? []).map((seat) => ({ ...seat })),
    });
    hasKey.value = config.has_key;
    history.value = records.items;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}

async function save() {
  busy.value = "save"; error.value = ""; notice.value = "";
  try {
    await v1.saveAiConfig({ ...form, timeout_s: Number(form.timeout_s), news_interval_s: Number(form.news_interval_s) });
    await load();
    notice.value = "模型配置已保存。";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败";
  } finally { busy.value = ""; }
}

function providerChanged() {
  form.endpoint = endpoints[form.provider] ?? "";
}

function addSeat() {
  if (form.council_seats.length >= 2) return;
  const role = form.council_seats.some((seat) => seat.role === "risk") ? "news" : "risk";
  form.council_seats.push({ role, provider: "openai", model: "", endpoint: endpoints.openai!, api_key_env: "OQ_AI_KEY_PRIMARY", timeout_s: 20 });
}

function actionLabel(action: string | undefined): string {
  return ({ wait: "观望", long: "做多提案", short: "做空提案" } as Record<string, string>)[action ?? ""] ?? "无有效提案";
}

function executionLabel(execution: string | undefined): string {
  return ({ research_only: "手动研究，不下单", paper_candidate_only: "纸面候选，结果以风控及订单记录为准", paper_strategy_confirmation_only: "纸面策略确认；是否下单以风控和订单记录为准" } as Record<string, string>)[execution ?? ""] ?? "执行状态未记录";
}

async function testConnection() {
  busy.value = "test"; error.value = ""; notice.value = "";
  try {
    const result = await v1.testAi();
    notice.value = `${result.provider} / ${result.model} 测试通过；测试使用服务器上已保存的配置。`;
  } catch (e) { error.value = e instanceof Error ? e.message : "连接测试失败"; }
  finally { busy.value = ""; }
}

async function analyze() {
  busy.value = "analyze"; error.value = ""; notice.value = "";
  try {
    const result = await v1.aiAnalyze(symbol.value.trim().toUpperCase());
    history.value = (await v1.aiHistory()).items;
    if (result.status === "completed") notice.value = "分析已完成，结果记录在下方；此操作不提交订单。";
    else error.value = result.error || "分析未完成，请查看下方记录。";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "分析失败";
    try { history.value = (await v1.aiHistory()).items; } catch { /* retain original error */ }
  } finally { busy.value = ""; }
}

function time(ms: number | null | undefined): string {
  return ms ? new Date(ms).toLocaleString("zh-CN", { hour12: false }) : "未知";
}
onMounted(load);
</script>

<template>
  <div>
    <PageHeader title="AI 模型与决策" subtitle="配置模型连接并查看分析记录。模型输出需要独立验证。" />
    <p v-if="error" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ error }}</p>
    <p v-if="notice" class="mb-4 rounded-lg border border-accent-600/40 bg-accent-600/10 px-3 py-2 text-sm text-accent-400">{{ notice }}</p>
    <div class="grid gap-4 xl:grid-cols-2">
      <form class="card card-pad space-y-4" @submit.prevent="save">
        <div>
          <h2 class="text-sm font-semibold">模型连接</h2>
          <p class="mt-1 text-xs text-mute-400">密钥由服务器环境变量提供，页面只配置变量名称，不显示密钥内容。</p>
        </div>
        <div v-if="loading" class="text-sm text-mute-400">加载中…</div>
        <template v-else>
          <label class="flex items-center gap-2 text-sm text-mute-300"><input v-model="form.enabled" type="checkbox" />启用模型分析</label>
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="space-y-1 text-xs text-mute-400">供应商
              <select v-model="form.provider" class="input w-full text-sm" @change="providerChanged"><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="anthropic">Anthropic</option></select>
            </label>
            <label class="space-y-1 text-xs text-mute-400">模型名称<input v-model="form.model" class="input w-full text-sm" autocomplete="off" /></label>
          </div>
          <label class="block space-y-1 text-xs text-mute-400">接口地址<input v-model="form.endpoint" type="url" class="input w-full text-sm" autocomplete="off" readonly /></label>
          <p class="text-xs text-mute-400">使用所选供应商的固定官方接口；环境变量名称和模型需在服务器中预先配置。</p>
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="space-y-1 text-xs text-mute-400">密钥环境变量名称<input v-model="form.api_key_env" class="input w-full text-sm" placeholder="例如 OQ_AI_KEY_PRIMARY" autocomplete="off" /></label>
            <label class="space-y-1 text-xs text-mute-400">超时（秒）<input v-model.number="form.timeout_s" type="number" min="2" max="60" class="input w-full text-sm" /></label>
          </div>
          <div class="text-xs text-mute-400">服务器密钥状态：<span :class="hasKey ? 'text-accent-400' : 'text-warn-500'">{{ hasKey ? '已配置' : '未检测到' }}</span></div>
          <section class="rounded-lg border border-ink-800 p-3 text-xs text-mute-300">
            <label class="flex items-center gap-2"><input v-model="form.council_enabled" type="checkbox" />启用多模型复核</label>
            <p class="mt-1.5 leading-relaxed text-mute-400">最多设置两个复核席位；任一席位失败或方向不一致时不生成开仓提案。每个启用席位会产生独立模型调用。</p>
            <div v-for="(seat, index) in form.council_seats" :key="index" class="mt-3 space-y-2 rounded-lg border border-ink-700 p-3">
              <div class="flex items-center justify-between"><span class="font-medium text-paper">复核席位 {{ index + 1 }}</span><button type="button" class="text-mute-400 hover:text-danger-500" @click="form.council_seats.splice(index, 1)">移除</button></div>
              <div class="grid gap-2 sm:grid-cols-2">
                <label class="space-y-1 text-mute-400">分析角色<select v-model="seat.role" class="input"><option value="market">市场</option><option value="news">新闻</option><option value="risk">风险</option></select></label>
                <label class="space-y-1 text-mute-400">供应商<select v-model="seat.provider" class="input" @change="seat.endpoint = endpoints[seat.provider] ?? ''"><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="anthropic">Anthropic</option></select></label>
                <label class="space-y-1 text-mute-400">模型名称<input v-model="seat.model" class="input" autocomplete="off" /></label>
                <label class="space-y-1 text-mute-400">密钥环境变量名称<input v-model="seat.api_key_env" class="input" placeholder="OQ_AI_KEY_..." autocomplete="off" /></label>
                <label class="space-y-1 text-mute-400">超时（秒）<input v-model.number="seat.timeout_s" class="input" type="number" min="2" max="60" /></label>
              </div>
              <p class="break-all text-mute-500">{{ seat.endpoint }}</p>
            </div>
            <button v-if="form.council_seats.length < 2" type="button" class="btn mt-3 text-xs" @click="addSeat">添加复核席位</button>
          </section>
          <div class="rounded-lg border border-ink-800 p-3 text-xs text-mute-300">
            <label class="flex items-center gap-2"><input v-model="form.auto_paper_enabled" type="checkbox" :disabled="form.paper_confirmation_enabled" />启用纸面模式独立 AI 提案</label>
            <p class="mt-1.5 leading-relaxed text-mute-400">仅在 paper 模式和模型启用时生效，每周期分析一个标的并经过风险检查。周期最多等待 15 秒，迟到结果不会执行，但模型调用仍可能产生费用；此功能不会执行真实交易。</p>
          </div>
          <div class="rounded-lg border border-ink-800 p-3 text-xs text-mute-300">
            <label class="flex items-center gap-2"><input v-model="form.paper_confirmation_enabled" type="checkbox" :disabled="form.auto_paper_enabled" />启用纸面策略的 AI 方向确认</label>
            <p class="mt-1.5 leading-relaxed text-mute-400">仅在 paper 模式生效，只确认下方指定标的的单腿方向策略入场；其他标的、套利及退出不受此确认控制。AI 失败、超时、观望或方向不一致时，该入场不执行；确认通过后仍需经过风控。此项与独立 AI 提案互斥，请先关闭另一项后再选择。</p>
          </div>
          <label class="block space-y-1 text-xs text-mute-400">纸面 AI 标的（适用于独立提案或策略确认）<input v-model="form.auto_symbol" class="input w-full text-sm" spellcheck="false" /></label>
          <div class="rounded-lg border border-ink-800 p-3 text-xs text-mute-300">
            <label class="flex items-center gap-2"><input v-model="form.auto_news_enabled" type="checkbox" />启用新闻自动采集</label>
            <p class="mt-1.5 leading-relaxed text-mute-400">启用后后台按间隔请求固定公共新闻源，不发送账户信息；关闭时仍可在新闻页手动刷新。</p>
            <label class="mt-2 block space-y-1 text-xs text-mute-400">采集间隔（秒）<input v-model.number="form.news_interval_s" type="number" min="300" max="86400" class="input w-full text-sm" /></label>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-primary text-xs" type="submit" :disabled="!!busy">{{ busy === 'save' ? '保存中…' : '保存配置' }}</button>
            <button class="btn text-xs" type="button" :disabled="!!busy" @click="testConnection">{{ busy === 'test' ? '测试中…' : '测试已保存的配置' }}</button>
          </div>
          <p class="text-xs text-mute-500">连接测试会向模型供应商发起请求并可能产生费用。</p>
        </template>
      </form>
      <div class="card card-pad space-y-4">
        <div><h2 class="text-sm font-semibold">手动分析</h2><p class="mt-1 text-xs leading-relaxed text-mute-400">使用当前可用的行情和新闻快照调用已配置模型；模型请求会产生供应商费用。此分析不会下单。</p></div>
        <label class="block space-y-1 text-xs text-mute-400">交易标的<input v-model="symbol" class="input w-full text-sm" spellcheck="false" /></label>
        <button class="btn text-xs" :disabled="!!busy || !symbol.trim()" @click="analyze">{{ busy === 'analyze' ? '分析中…' : '运行分析' }}</button>
        <div class="rounded-lg bg-ink-800/50 p-3 text-xs leading-relaxed text-mute-400">分析结果是研究建议；交易是否发生以订单和风控记录为准。</div>
      </div>
    </div>
    <section class="card mt-4">
      <div class="border-b border-ink-800 px-4 py-3"><h2 class="text-sm font-semibold">分析记录</h2></div>
      <div v-if="!history.length" class="p-5 text-sm text-mute-500">暂无分析记录。</div>
      <article v-for="record in history" :key="record.id" class="border-b border-ink-800 p-4 last:border-0">
        <div class="flex flex-wrap items-center justify-between gap-2 text-xs"><div class="flex items-center gap-2"><span class="font-medium text-paper">{{ record.symbol }}</span><span class="chip" :class="record.status === 'completed' ? 'bg-accent-600/10 text-accent-400' : 'bg-danger-600/10 text-danger-500'">{{ record.status === 'completed' ? actionLabel(record.proposal?.action) : '分析失败' }}</span></div><span class="text-mute-500">{{ time(record.ts_ms) }} · {{ record.provider }} / {{ record.model }}</span></div>
        <p class="mt-2 text-xs text-mute-400">{{ executionLabel(record.execution) }}</p>
        <p v-if="record.execution === 'paper_strategy_confirmation_only'" class="mt-1 text-xs text-mute-400">策略：{{ record.strategy_id ?? '未知' }} · 方向确认：{{ record.confirmed === true ? '通过' : record.confirmed === false ? '未通过' : '未知' }} · {{ record.confirmation_reason ?? '未记录原因' }}</p>
        <p v-if="record.error" class="mt-2 text-xs text-danger-500">{{ record.error }}</p>
        <template v-if="record.proposal"><p v-if="record.proposal.rationale" class="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed text-mute-300">{{ record.proposal.rationale }}</p><p class="mt-2 text-xs text-mute-400">止损参考：<span class="tnum">{{ record.proposal.stop_px ?? '—' }}</span> · 快照参考价：<span class="tnum">{{ record.snapshot?.reference_price ?? '—' }}</span></p></template>
        <div v-if="record.council?.length" class="mt-3 grid gap-2 sm:grid-cols-2"><div v-for="(seat, index) in record.council" :key="index" class="rounded border border-ink-800 bg-ink-950 p-3 text-xs text-mute-400"><span class="text-paper">{{ seat.role }} · {{ seat.provider }} / {{ seat.model }}</span><p class="mt-1">{{ seat.status }} · {{ actionLabel(seat.proposal?.action) }}</p><p v-if="seat.proposal?.rationale" class="mt-1 whitespace-pre-wrap break-words leading-relaxed">{{ seat.proposal.rationale }}</p></div></div>
        <details class="mt-3 text-xs text-mute-500"><summary class="cursor-pointer hover:text-paper">查看快照与调用详情</summary><p class="mt-2">采集：{{ time(record.snapshot?.captured_ms) }} · 最新 K 线：{{ time(record.snapshot?.last_candle_open_ms) }} · 数据来源：{{ record.snapshot?.data_source ?? '未知' }}</p><p class="mt-1 break-all">快照指纹：{{ record.snapshot_hash ?? '未知' }} · Token：{{ record.usage?.total_tokens ?? '未知' }}</p><pre class="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-ink-950 p-3 text-mute-300">{{ JSON.stringify(record, null, 2) }}</pre></details>
      </article>
    </section>
  </div>
</template>
