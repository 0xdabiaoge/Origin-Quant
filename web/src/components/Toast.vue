<script setup lang="ts">
import { useToast } from "@/components/useToast";
const { items, dismiss } = useToast();
</script>

<template>
  <div class="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
    <TransitionGroup
      enter-active-class="transition duration-200"
      enter-from-class="translate-y-2 opacity-0"
      leave-active-class="transition duration-150"
      leave-to-class="opacity-0"
    >
      <div
        v-for="t in items"
        :key="t.id"
        class="pointer-events-auto flex min-w-[240px] max-w-sm items-start gap-3 rounded-lg border px-3.5 py-2.5 text-sm shadow-lg"
        :class="{
          'border-accent-600/50 bg-ink-850 text-accent-400': t.kind === 'ok',
          'border-danger-600/50 bg-ink-850 text-danger-500': t.kind === 'error',
          'border-ink-600 bg-ink-850 text-mute-300': t.kind === 'info',
        }"
        @click="dismiss(t.id)"
      >
        <span class="flex-1">{{ t.text }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>
