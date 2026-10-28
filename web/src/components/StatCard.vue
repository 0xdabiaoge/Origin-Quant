<script setup lang="ts">
defineProps<{
  label: string;
  value: string;
  hint?: string;
  tone?: "neutral" | "up" | "down" | "warn";
}>();
</script>

<template>
  <div class="card card-pad">
    <div class="label">{{ label }}</div>
    <div
      class="stat mt-2"
      :class="{
        'text-accent-400': tone === 'up',
        'text-danger-500': tone === 'down',
        'text-warn-500': tone === 'warn',
      }"
    >
      {{ value }}
    </div>
    <div v-if="hint" class="mt-1.5 text-xs text-mute-400">{{ hint }}</div>
  </div>
</template>
