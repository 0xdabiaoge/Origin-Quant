import { defineStore } from "pinia";
import { ref } from "vue";
import { v1, withoutUnauthorizedRedirect } from "@/api/client";
import type { Me } from "@/api/types";

export const useAuthStore = defineStore("auth", () => {
  const user = ref<Me | null>(null);
  const ready = ref(false);
  const error = ref<string>("");

  async function refresh(): Promise<void> {
    try {
      // A logged-out visitor is the normal case here, not an expired session.
      user.value = await withoutUnauthorizedRedirect(() => v1.me());
    } catch {
      user.value = null;
    } finally {
      ready.value = true;
    }
  }

  async function login(username: string, password: string): Promise<boolean> {
    error.value = "";
    try {
      const res = await v1.login(username, password);
      user.value = { username: res.username, role: res.role, via: "cookie" };
      return true;
    } catch (err) {
      error.value = err instanceof Error ? err.message : "登录失败";
      return false;
    }
  }

  async function logout(): Promise<void> {
    try {
      await v1.logout();
    } finally {
      user.value = null;
    }
  }

  function can(role: "viewer" | "trader" | "owner"): boolean {
    const rank = { viewer: 0, trader: 1, owner: 2 } as const;
    const mine = user.value?.role;
    if (!mine) return false;
    return rank[mine as keyof typeof rank] >= rank[role];
  }

  return { user, ready, error, refresh, login, logout, can };
});
