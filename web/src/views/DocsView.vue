<script setup lang="ts">
import { RouterLink } from "vue-router";

interface Section {
  id: string;
  title: string;
  body: string[];
  code?: string;
}

const sections: Section[] = [
  {
    id: "intro",
    title: "系统是什么",
    body: [
      "Origin Quant 是一个面向自用的量化交易工作台，包含行情、策略、风险检查、订单管理与审计。",
      "AI 研究结果、纸面模拟与实盘交易是不同的运行过程，应以页面显示的实际状态为准。",
    ],
  },
  {
    id: "quickstart",
    title: "快速开始（本地）",
    body: ["安装依赖并启动控制面。合成数据回测在内存中生成数据；不要在已有行情目录中运行合成数据灌入命令，它可能覆盖数据。"],
    code: `python -m venv .venv
.venv/Scripts/activate        # Windows
source .venv/bin/activate     # Linux / macOS
pip install -e ".[dev]"

oq backtest --source synthetic --days 30
oq serve                      # http://127.0.0.1:8000`,
  },
  {
    id: "modes",
    title: "运行模式与安全闸门",
    body: [
      "系统支持纸面和实盘模式。实盘下单需要 live 模式、配置中的 live_trading=true 和 OQ_LIVE=1 同时满足；交易所 demo / live 环境也应单独核对。",
      "接入实盘前应检查实际生效配置、账户环境、风控和交易所保护状态，并在隔离模拟环境验证。",
    ],
    code: `# config/base.yaml
app:
  mode: paper           # paper | live
  live_trading: false

# 环境变量
OQ_LIVE=0`,
  },
  {
    id: "books",
    title: "双账本",
    body: [
      "系统按 carry（套利/资金费率）和 directional（方向性）分别记录交易；两者共享账户权益、总杠杆等组合风险上限。",
      "退出意图通过结构性风控检查；数据缺失、执行失败或交易所不可用仍可能影响平仓，应核对实际订单与持仓。",
    ],
  },
  {
    id: "roles",
    title: "角色与权限",
    body: [
      "owner：全部权限，包含用户管理、配置查看与危险操作区。",
      "trader：可以驱动引擎，包括暂停、恢复、熔断、平仓和手动跑周期；不能管理用户或解除熔断。",
      "viewer：只读。",
      "首次部署时用 OQ_ADMIN_PASSWORD_HASH 提供的密码以 operator 身份登录，之后建议在管理后台创建正式账号。",
    ],
  },
  {
    id: "api",
    title: "HTTP 接口",
    body: [
      "接口分三组：/api/v1/public 无需登录，/api/v1/app 需要登录，/api/v1/admin 需要 owner。旧的 /api/* 接口保留兼容。",
      "交互式文档在 /api/docs。",
    ],
    code: `GET  /api/v1/public/brand        # 品牌与版本，无需登录
GET  /api/v1/public/metrics      # 公开指标按服务端配置提供
GET  /api/v1/app/overview        # 工作台总览
GET  /api/v1/admin/users         # 用户列表（owner）
POST /api/v1/admin/danger/kill    # 熔断（需输入确认词）`,
  },
  {
    id: "deploy",
    title: "部署到服务器",
    body: [
      "部署前应备份数据库、行情与配置；检查目标镜像、部署环境和回滚方式，完成健康及实际行为验证。",
    ],
  },
  {
    id: "limits",
    title: "已知限制",
    body: [
      "模拟、合成数据回测与真实交易的结果不同，AI 分析也不保证收益。",
      "公开部署与实盘前，应独立验证权限、订单恢复、保护单和交易所账户状态。",
      "单进程意味着单点故障：进程挂掉，整个交易链路停止。这对单操作员场景是可接受的取舍。",
    ],
  },
];
</script>

<template>
  <div class="min-h-screen bg-ink-950">
    <header class="sticky top-0 z-30 border-b border-ink-800/80 bg-ink-950/80 backdrop-blur">
      <div class="mx-auto flex h-14 max-w-5xl items-center justify-between px-4 sm:px-6">
        <RouterLink to="/" class="flex items-center gap-2.5">
          <svg viewBox="0 0 32 32" class="h-7 w-7">
            <rect width="32" height="32" rx="8" fill="#0f141b" />
            <path d="M8 22 L14 12 L19 17 L24 9" stroke="#34d399" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <span class="text-sm font-semibold tracking-tight">Origin Quant</span>
        </RouterLink>
        <nav class="flex items-center gap-1">
          <RouterLink to="/" class="btn btn-ghost text-mute-300">首页</RouterLink>
          <RouterLink to="/app" class="btn btn-primary">工作台</RouterLink>
        </nav>
      </div>
    </header>

    <div class="mx-auto flex max-w-5xl gap-10 px-4 py-10 sm:px-6">
      <aside class="hidden w-44 shrink-0 lg:block">
        <div class="sticky top-24 space-y-0.5">
          <div class="label mb-2 px-2">目录</div>
          <a
            v-for="s in sections"
            :key="s.id"
            :href="`#${s.id}`"
            class="block rounded-md px-2 py-1.5 text-xs text-mute-400 transition hover:bg-ink-850 hover:text-paper"
          >
            {{ s.title }}
          </a>
        </div>
      </aside>

      <main class="min-w-0 flex-1">
        <h1 class="text-2xl font-semibold tracking-tight">使用文档</h1>
        <p class="mt-2 text-sm text-mute-400">
          这里写的是这套系统真实的行为，包括它的限制。
        </p>

        <div class="mt-9 space-y-10">
          <section v-for="s in sections" :id="s.id" :key="s.id" class="scroll-mt-24">
            <h2 class="text-base font-semibold text-paper">{{ s.title }}</h2>
            <p v-for="(p, i) in s.body" :key="i" class="mt-2.5 text-sm leading-relaxed text-mute-300">
              {{ p }}
            </p>
            <pre
              v-if="s.code"
              class="mt-3.5 overflow-x-auto rounded-lg border border-ink-700 bg-ink-900 p-3.5 text-[12px] leading-relaxed text-mute-300"
            ><code class="tnum">{{ s.code }}</code></pre>
          </section>
        </div>
      </main>
    </div>
  </div>
</template>
