<script setup lang="ts">
import { RouterLink } from "vue-router";
</script>

<template>
  <div class="flex min-h-screen flex-col items-center justify-center gap-4 bg-ink-950 px-4 text-center">
    <div class="tnum text-5xl font-semibold text-ink-600">404</div>
    <div class="text-sm text-mute-300">这个地址不存在</div>
    <div class="flex gap-2">
      <RouterLink to="/" class="btn">返回首页</RouterLink>
      <RouterLink to="/app" class="btn btn-primary">进入工作台</RouterLink>
    </div>
  </div>
</template>
