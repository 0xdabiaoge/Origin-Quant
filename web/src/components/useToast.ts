import { ref } from "vue";

export type ToastKind = "ok" | "error" | "info";
export interface ToastItem {
  id: number;
  text: string;
  kind: ToastKind;
}

const items = ref<ToastItem[]>([]);
let seq = 0;

function push(text: string, kind: ToastKind = "info", ttl = 3600): void {
  const id = ++seq;
  items.value.push({ id, text, kind });
  window.setTimeout(() => dismiss(id), ttl);
}

function dismiss(id: number): void {
  items.value = items.value.filter((t) => t.id !== id);
}

export function useToast() {
  return {
    items,
    dismiss,
    ok: (t: string) => push(t, "ok"),
    error: (t: string) => push(t, "error", 6000),
    info: (t: string) => push(t, "info"),
  };
}
