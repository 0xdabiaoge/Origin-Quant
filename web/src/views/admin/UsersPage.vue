<script setup lang="ts">
import { onMounted, ref } from "vue";
import { v1 } from "@/api/client";
import type { UserRow } from "@/api/types";
import { fmtTime, fmtAgo } from "@/api/format";
import PageHeader from "@/components/PageHeader.vue";
import EmptyState from "@/components/EmptyState.vue";
import { useToast } from "@/components/useToast";

const users = ref<UserRow[]>([]);
const roles = ref<string[]>([]);
const err = ref("");
const toast = useToast();

const showCreate = ref(false);
const form = ref({ username: "", password: "", role: "viewer" });
const busy = ref(false);
const pwTarget = ref<string | null>(null);
const newPassword = ref("");

async function load() {
  try {
    const res = await v1.users();
    users.value = res.items;
    roles.value = res.roles;
  } catch (e) {
    err.value = e instanceof Error ? e.message : "加载失败";
  }
}
onMounted(load);

async function create() {
  busy.value = true;
  try {
    await v1.createUser(form.value);
    toast.ok(`已创建用户 ${form.value.username}`);
    showCreate.value = false;
    form.value = { username: "", password: "", role: "viewer" };
    await load();
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "创建失败");
  } finally {
    busy.value = false;
  }
}

async function changeRole(u: UserRow, role: string) {
  try {
    await v1.setRole(u.username, role);
    toast.ok(`${u.username} 角色改为 ${role}`);
    await load();
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "修改失败");
  }
}

async function savePassword() {
  if (!pwTarget.value) return;
  try {
    await v1.setPassword(pwTarget.value, newPassword.value);
    toast.ok("密码已更新");
    pwTarget.value = null;
    newPassword.value = "";
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "更新失败");
  }
}

async function remove(u: UserRow) {
  if (!confirm(`确认删除用户 ${u.username}？此操作不可撤销。`)) return;
  try {
    await v1.deleteUser(u.username);
    toast.ok("已删除");
    await load();
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "删除失败");
  }
}

const roleTone: Record<string, string> = {
  owner: "bg-danger-600/10 text-danger-500",
  trader: "bg-warn-500/10 text-warn-500",
  viewer: "bg-info-500/10 text-info-500",
};
</script>

<template>
  <div>
    <PageHeader title="用户" subtitle="owner 可管理全部；trader 可驱动引擎；viewer 只读。">
      <template #actions>
        <button class="btn btn-primary" @click="showCreate = !showCreate">新建用户</button>
      </template>
    </PageHeader>

    <p v-if="err" class="mb-4 rounded-lg border border-danger-600/40 bg-danger-600/10 px-3 py-2 text-sm text-danger-500">{{ err }}</p>

    <div v-if="showCreate" class="card card-pad mb-3">
      <div class="grid gap-3 sm:grid-cols-3">
        <div>
          <label class="label mb-1.5 block">用户名</label>
          <input v-model="form.username" class="input" placeholder="alice" spellcheck="false" />
        </div>
        <div>
          <label class="label mb-1.5 block">密码（至少 8 位）</label>
          <input v-model="form.password" type="password" class="input" />
        </div>
        <div>
          <label class="label mb-1.5 block">角色</label>
          <select v-model="form.role" class="input">
            <option v-for="r in roles.length ? roles : ['owner','trader','viewer']" :key="r" :value="r">{{ r }}</option>
          </select>
        </div>
      </div>
      <div class="mt-3 flex gap-2">
        <button class="btn btn-primary" :disabled="busy || !form.username || form.password.length < 8" @click="create">创建</button>
        <button class="btn btn-ghost" @click="showCreate = false">取消</button>
      </div>
    </div>

    <div class="card">
      <div v-if="users.length" class="overflow-x-auto">
        <table class="table-base">
          <thead>
            <tr><th>ID</th><th>用户名</th><th>角色</th><th>创建时间</th><th>最近登录</th><th class="text-right">操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td class="tnum text-xs text-mute-500">{{ u.id }}</td>
              <td class="text-xs font-medium">{{ u.username }}</td>
              <td>
                <select
                  class="rounded border border-ink-600 bg-ink-850 px-1.5 py-0.5 text-[11px]"
                  :class="roleTone[u.role] ?? ''"
                  :value="u.role"
                  @change="changeRole(u, ($event.target as HTMLSelectElement).value)"
                >
                  <option v-for="r in ['owner','trader','viewer']" :key="r" :value="r">{{ r }}</option>
                </select>
              </td>
              <td class="tnum text-xs text-mute-400">{{ fmtTime(u.created_ms) }}</td>
              <td class="text-xs text-mute-400">{{ u.last_login_ms ? fmtAgo(u.last_login_ms) : '从未' }}</td>
              <td class="text-right">
                <div class="flex justify-end gap-1">
                  <button class="btn btn-ghost px-2 py-1 text-[11px]" @click="pwTarget = u.username; newPassword = ''">改密</button>
                  <button class="btn btn-ghost px-2 py-1 text-[11px] text-danger-500" @click="remove(u)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else title="没有用户" hint="当前只有环境变量提供的内置密码可用。建议创建一个正式账号。" />
    </div>

    <div v-if="pwTarget" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4" @click.self="pwTarget = null">
      <div class="card card-pad w-full max-w-sm">
        <div class="text-sm font-medium">修改 {{ pwTarget }} 的密码</div>
        <input v-model="newPassword" type="password" class="input mt-3" placeholder="新密码（至少 8 位）" />
        <div class="mt-3 flex justify-end gap-2">
          <button class="btn btn-ghost" @click="pwTarget = null">取消</button>
          <button class="btn btn-primary" :disabled="newPassword.length < 8" @click="savePassword">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
