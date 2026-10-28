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
      // /auth/me answers 200 with authenticated:false rather than 401, so an
      // anonymous page load does not litter the console with an error.
      const me = await withoutUnauthorizedRedirect(() => v1.me());
      user.value = me.authenticated
        ? ({ ...me } as Me)
        : null;
    } catch {
      user.value = null;
    } finally {
      ready.value = true;
    }
  }

  async function login(username: string, password: string): Promise<boolean> {
    error.value = "";
    try {
      await v1.login(username, password);
      // Re-read the session from the server so the role is never something the
      // client guessed from the login response.
      await refresh();
      return user.value !== null;
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
