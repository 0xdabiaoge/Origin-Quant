<script setup lang="ts">
withDefaults(defineProps<{ ok?: boolean; label?: string; pulse?: boolean }>(), {
  ok: true,
  pulse: false,
});
</script>

<template>
  <span class="inline-flex items-center gap-2 text-xs text-mute-300">
    <span class="relative flex h-2 w-2">
      <span
        v-if="pulse"
        class="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
        :class="ok ? 'bg-accent-500' : 'bg-danger-500'"
      />
      <span
        class="relative inline-flex h-2 w-2 rounded-full"
        :class="ok ? 'bg-accent-500' : 'bg-danger-500'"
      />
    </span>
    <span v-if="label">{{ label }}</span>
  </span>
</template>
