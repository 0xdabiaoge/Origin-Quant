<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { v1 } from "@/api/client";
import StatusDot from "@/components/StatusDot.vue";
import Toast from "@/components/Toast.vue";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const open = ref(false);
const mode = ref("");
const version = ref("");

const nav = [
  { name: "app-overview", label: "总览", icon: "M3 12h4l3 8 4-16 3 8h4" },
  { name: "app-positions", label: "持仓", icon: "M4 6h16M4 12h16M4 18h10" },
  { name: "app-orders", label: "订单 / 成交", icon: "M6 3h9l3 3v15H6zM9 12h6M9 16h6" },
  { name: "app-decisions", label: "决策流水", icon: "M4 4h16v6H4zM4 14h10v6H4z" },
  { name: "app-backtest", label: "回测", icon: "M4 18V8m5 10V4m5 14v-7m5 7V6" },
  { name: "app-data", label: "数据", icon: "M12 3v18M3 12h18" },
];

const isActive = (name: string) => route.name === name;

onMounted(async () => {
  try {
    const b = await v1.brand();
    mode.value = b.mode;
    version.value = b.version;
  } catch {
    /* brand is cosmetic; never block the shell on it */
  }
});

async function doLogout() {
  await auth.logout();
  void router.push({ name: "login" });
}

const roleLabel = computed(
  () => ({ owner: "所有者", trader: "交易员", viewer: "只读" })[auth.user?.role ?? ""] ?? auth.user?.role,
);
</script>

<template>
  <div class="flex min-h-screen bg-ink-950">
    <!-- sidebar -->
    <aside
      class="fixed inset-y-0 left-0 z-40 w-60 shrink-0 border-r border-ink-800 bg-ink-900
             transition-transform lg:static lg:translate-x-0"
      :class="open ? 'translate-x-0' : '-translate-x-full'"
    >
      <div class="flex h-14 items-center gap-2.5 border-b border-ink-800 px-4">
        <svg viewBox="0 0 32 32" class="h-7 w-7 shrink-0">
          <rect width="32" height="32" rx="8" fill="#0b0f14" />
          <path
            d="M8 22 L14 12 L19 17 L24 9"
            stroke="#34d399"
            stroke-width="2.5"
            fill="none"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        <div class="min-w-0">
          <div class="truncate text-sm font-semibold tracking-tight">Origin Quant</div>
          <div class="truncate text-[11px] text-mute-500">v{{ version || "—" }}</div>
        </div>
      </div>

      <nav class="flex flex-col gap-0.5 p-2.5">
        <RouterLink
          v-for="item in nav"
          :key="item.name"
          :to="{ name: item.name }"
          class="nav-link"
          :class="{ 'nav-link-active': isActive(item.name) }"
          @click="open = false"
        >
          <svg viewBox="0 0 24 24" class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" stroke-width="1.8">
            <path :d="item.icon" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          {{ item.label }}
        </RouterLink>

        <div class="my-2 border-t border-ink-800" />

        <RouterLink
          v-if="auth.can('owner')"
          :to="{ name: 'admin-overview' }"
          class="nav-link"
          @click="open = false"
        >
          <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8">
            <path
              d="M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7z"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          管理后台
        </RouterLink>
        <RouterLink :to="{ name: 'docs' }" class="nav-link" @click="open = false">
          <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M4 5h16v14H4zM8 9h8M8 13h5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          文档
        </RouterLink>
      </nav>

      <div class="absolute inset-x-0 bottom-0 border-t border-ink-800 p-3">
        <div class="mb-2 flex items-center justify-between">
          <div class="min-w-0">
            <div class="truncate text-xs font-medium">{{ auth.user?.username }}</div>
            <div class="text-[11px] text-mute-500">{{ roleLabel }}</div>
          </div>
          <StatusDot :ok="mode === 'paper' || mode === 'live'" :label="mode" />
        </div>
        <button class="btn btn-ghost w-full justify-start text-xs" @click="doLogout">退出登录</button>
      </div>
    </aside>

    <div v-if="open" class="fixed inset-0 z-30 bg-black/60 lg:hidden" @click="open = false" />

    <!-- main -->
    <div class="flex min-w-0 flex-1 flex-col">
      <header class="flex h-14 items-center gap-3 border-b border-ink-800 px-4 lg:hidden">
        <button class="btn btn-ghost px-2" @click="open = !open">
          <svg viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M4 7h16M4 12h16M4 17h16" stroke-linecap="round" />
          </svg>
        </button>
        <span class="text-sm font-semibold">Origin Quant</span>
      </header>

      <main class="flex-1 px-4 py-5 sm:px-6 sm:py-6">
        <RouterView />
      </main>
    </div>

    <Toast />
  </div>
</template>
