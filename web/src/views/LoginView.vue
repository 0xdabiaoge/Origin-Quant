<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();

const username = ref("operator");
const password = ref("");
const busy = ref(false);

async function submit() {
  if (busy.value) return;
  busy.value = true;
  const ok = await auth.login(username.value.trim(), password.value);
  busy.value = false;
  if (ok) {
    const next = typeof route.query.next === "string" ? route.query.next : "/app";
    void router.push(next);
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-ink-950 px-4">
    <div class="w-full max-w-sm">
      <div class="mb-8 flex flex-col items-center gap-3">
        <svg viewBox="0 0 32 32" class="h-12 w-12">
          <rect width="32" height="32" rx="8" fill="#0f141b" />
          <path
            d="M8 22 L14 12 L19 17 L24 9"
            stroke="#34d399"
            stroke-width="2.5"
            fill="none"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        <div class="text-center">
          <h1 class="text-lg font-semibold tracking-tight">Origin Quant</h1>
          <p class="mt-1 text-xs text-mute-400">单操作员加密货币量化交易引擎</p>
        </div>
      </div>

      <form class="card card-pad space-y-4" @submit.prevent="submit">
        <div>
          <label class="label mb-1.5 block" for="u">用户名</label>
          <input id="u" v-model="username" class="input" autocomplete="username" spellcheck="false" />
        </div>
        <div>
          <label class="label mb-1.5 block" for="p">密码</label>
          <input
            id="p"
            v-model="password"
            type="password"
            class="input"
            autocomplete="current-password"
            placeholder="••••••••"
          />
        </div>

        <p v-if="auth.error" class="rounded-md bg-danger-600/10 px-2.5 py-2 text-xs text-danger-500">
          {{ auth.error }}
        </p>

        <button type="submit" class="btn btn-primary w-full" :disabled="busy || !password">
          {{ busy ? "验证中…" : "登录" }}
        </button>
      </form>

      <p class="mt-5 text-center text-[11px] leading-relaxed text-mute-500">
        默认用户名 <code class="tnum text-mute-400">operator</code>，密码为部署时设置的
        <code class="tnum text-mute-400">OQ_ADMIN_PASSWORD_HASH</code>。
      </p>
    </div>
  </div>
</template>