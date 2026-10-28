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
      "Origin Quant 是一个单进程、单写者的加密货币量化交易引擎。信号生成、风控裁决、订单执行、成交对账与审计日志都在同一个进程内完成，共享同一个时钟。",
      "它刻意不采用微服务架构：在单机交易场景里，分布式带来的时钟漂移、消息重放与部分失败，只会制造更多需要解释的状态。",
    ],
  },
  {
    id: "quickstart",
    title: "快速开始（本地）",
    body: ["安装依赖并启动控制面。默认使用纸面撮合，不会下真实订单。"],
    code: `python -m venv .venv
.venv/Scripts/activate        # Windows
source .venv/bin/activate     # Linux / macOS
pip install -e ".[dev]"

oq seed --source synthetic --days 30
oq serve                      # http://127.0.0.1:8000`,
  },
  {
    id: "modes",
    title: "运行模式与安全闸门",
    body: [
      "系统有两道独立闸门保护实盘：配置文件里的 app.live_trading 必须为 true，同时环境变量 OQ_LIVE 必须为 1，且 mode 必须是 live。任意一道不满足，下单都会被拒绝。",
      "这是刻意的冗余：单一开关被误改的概率远高于两个不同来源的开关同时被误改。",
    ],
    code: `# config/app.toml
[app]
mode = "paper"          # paper | live
live_trading = false

# 环境变量
OQ_LIVE=0`,
  },
  {
    id: "books",
    title: "双账本",
    body: [
      "系统维护两个独立的账本：carry（套利/资金费率）与 directional（方向性）。两者各有自己的保证金占用与风险预算，互不挪用。",
      "平仓订单走结构性闸门，不会被开仓用的条件挡住——否则风控收紧时会出现无法平仓的死锁。",
    ],
  },
  {
    id: "roles",
    title: "角色与权限",
    body: [
      "owner：全部权限，包含用户管理、配置查看与危险操作区。",
      "trader：可以驱动引擎（暂停、恢复、手动跑周期），不能改配置或管理用户。",
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
GET  /api/v1/public/metrics      # 公开净值曲线，不含持仓
GET  /api/v1/app/overview        # 工作台总览
GET  /api/v1/admin/users         # 用户列表（owner）
POST /api/v1/admin/danger/kill    # 熔断（需输入确认词）`,
  },
  {
    id: "deploy",
    title: "部署到服务器",
    body: [
      "镜像由 CI 构建并推送到 GHCR，服务器只负责拉取。版本号由 v1.* 标签数量推导，每次合并到 main 自动递增。",
    ],
    code: `# 服务器上
cd /opt/origin-quant
git pull
OQ_IMAGE_TAG=v1.1.0 docker compose pull
docker compose up -d

# 回滚只需改回上一个标签
OQ_IMAGE_TAG=v1.0.2 docker compose up -d`,
  },
  {
    id: "limits",
    title: "已知限制",
    body: [
      "策略在合成数据上的期望值为负——成本（手续费 + 资金费）吃掉了全部边际。这是真实结论，不是配置问题。",
      "没有做压力测试与安全渗透测试。公网暴露前请自行评估。",
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