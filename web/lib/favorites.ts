"use client";

const KEY = "ob-ai-favorites-v1";

export function readFavorites(): Set<number> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) return new Set(arr.filter((x) => typeof x === "number"));
  } catch {}
  return new Set();
}

export function writeFavorites(ids: Set<number>) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KEY, JSON.stringify(Array.from(ids)));
  // 自定义事件，让其他组件订阅
  window.dispatchEvent(new CustomEvent("favorites-changed", { detail: ids.size }));
}

export function toggleFavorite(id: number): boolean {
  const cur = readFavorites();
  const wasIn = cur.has(id);
  if (wasIn) cur.delete(id);
  else cur.add(id);
  writeFavorites(cur);
  return !wasIn;
}

export function isFavorite(id: number): boolean {
  return readFavorites().has(id);
}
