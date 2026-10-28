<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { v1 } from "@/api/client";
import Toast from "@/components/Toast.vue";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const open = ref(false);
const version = ref("");
const mode = ref("");

const nav = [
  { name: "admin-overview", label: "系统概览" },
  { name: "admin-users", label: "用户" },
  { name: "admin-audit", label: "审计日志" },
  { name: "admin-config", label: "配置" },
  { name: "admin-logs", label: "运行日志" },
  { name: "admin-backup", label: "备份" },
  { name: "admin-danger", label: "危险操作" },
];

onMounted(async () => {
  try {
    const b = await v1.brand();
    version.value = b.version;
    mode.value = b.mode;
  } catch {
    /* cosmetic */
  }
});

async function doLogout() {
  await auth.logout();
  void router.push({ name: "login" });
}
</script>

<template>
  <div class="flex min-h-screen bg-ink-950">
    <aside
      class="fixed inset-y-0 left-0 z-40 w-56 shrink-0 border-r border-ink-800 bg-ink-900 transition-transform lg:static lg:translate-x-0"
      :class="open ? 'translate-x-0' : '-translate-x-full'"
    >
      <div class="flex h-14 items-center gap-2.5 border-b border-ink-800 px-4">
        <div class="flex h-7 w-7 items-center justify-center rounded-md bg-danger-600/15">
          <svg viewBox="0 0 24 24" class="h-4 w-4 text-danger-500" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7z" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </div>
        <div class="min-w-0">
          <div class="truncate text-sm font-semibold">管理后台</div>
          <div class="truncate text-[11px] text-mute-500">v{{ version || "—" }} · {{ mode || "—" }}</div>
        </div>
      </div>

      <nav class="flex flex-col gap-0.5 p-2.5">
        <RouterLink
          v-for="item in nav"
          :key="item.name"
          :to="{ name: item.name }"
          class="nav-link"
          :class="{ 'nav-link-active': route.name === item.name }"
          @click="open = false"
        >
          {{ item.label }}
        </RouterLink>

        <div class="my-2 border-t border-ink-800" />
        <RouterLink :to="{ name: 'app-overview' }" class="nav-link" @click="open = false">
          ← 返回工作台
        </RouterLink>
      </nav>

      <div class="absolute inset-x-0 bottom-0 border-t border-ink-800 p-3">
        <div class="mb-2 truncate text-xs text-mute-400">{{ auth.user?.username }}</div>
        <button class="btn btn-ghost w-full justify-start text-xs" @click="doLogout">退出登录</button>
      </div>
    </aside>

    <div v-if="open" class="fixed inset-0 z-30 bg-black/60 lg:hidden" @click="open = false" />

    <div class="flex min-w-0 flex-1 flex-col">
      <header class="flex h-14 items-center gap-3 border-b border-ink-800 px-4 lg:hidden">
        <button class="btn btn-ghost px-2" @click="open = !open">
          <svg viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M4 7h16M4 12h16M4 17h16" stroke-linecap="round" />
          </svg>
        </button>
        <span class="text-sm font-semibold">管理后台</span>
      </header>

      <main class="flex-1 px-4 py-5 sm:px-6 sm:py-6">
        <RouterView />
      </main>
    </div>

    <Toast />
  </div>
</template>