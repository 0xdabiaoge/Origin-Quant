<script setup lang="ts">
defineProps<{ title: string; hint?: string }>();
</script>

<template>
  <div class="flex flex-col items-center justify-center gap-2 px-4 py-12 text-center">
    <div class="text-sm font-medium text-mute-300">{{ title }}</div>
    <div v-if="hint" class="max-w-md text-xs leading-relaxed text-mute-500">{{ hint }}</div>
    <slot />
  </div>
</template>
