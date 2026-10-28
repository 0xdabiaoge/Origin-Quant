import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "landing", component: () => import("@/views/LandingView.vue") },
    { path: "/docs", name: "docs", component: () => import("@/views/DocsView.vue") },
    { path: "/login", name: "login", component: () => import("@/views/LoginView.vue") },
    {
      path: "/app",
      component: () => import("@/layouts/AppLayout.vue"),
      meta: { requiresAuth: true },
      children: [
        { path: "", name: "app-overview", component: () => import("@/views/app/OverviewView.vue") },
        { path: "positions", name: "app-positions", component: () => import("@/views/app/PositionsView.vue") },
        { path: "orders", name: "app-orders", component: () => import("@/views/app/OrdersView.vue") },
        { path: "decisions", name: "app-decisions", component: () => import("@/views/app/DecisionsView.vue") },
        { path: "backtest", name: "app-backtest", component: () => import("@/views/app/BacktestView.vue") },
        { path: "data", name: "app-data", component: () => import("@/views/app/DataView.vue") },
      ],
    },
    {
      path: "/admin",
      component: () => import("@/layouts/AdminLayout.vue"),
      meta: { requiresAuth: true, requiresOwner: true },
      children: [
        { path: "", name: "admin-overview", component: () => import("@/views/admin/OverviewPage.vue") },
        { path: "users", name: "admin-users", component: () => import("@/views/admin/UsersPage.vue") },
        { path: "audit", name: "admin-audit", component: () => import("@/views/admin/AuditPage.vue") },
        { path: "config", name: "admin-config", component: () => import("@/views/admin/ConfigPage.vue") },
        { path: "logs", name: "admin-logs", component: () => import("@/views/admin/LogsPage.vue") },
        { path: "backup", name: "admin-backup", component: () => import("@/views/admin/BackupPage.vue") },
        { path: "danger", name: "admin-danger", component: () => import("@/views/admin/DangerPage.vue") },
      ],
    },
    { path: "/:pathMatch(.*)*", name: "not-found", component: () => import("@/views/NotFoundView.vue") },
  ],
  scrollBehavior: () => ({ top: 0 }),
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!auth.ready) await auth.refresh();

  if (to.meta.requiresAuth && !auth.user) {
    return { name: "login", query: { next: to.fullPath } };
  }
  if (to.meta.requiresOwner && !auth.can("owner")) {
    return { name: "app-overview" };
  }
  if (to.name === "login" && auth.user) {
    return { name: "app-overview" };
  }
  return true;
});

export default router;
