"use client";

const KEY = "ob-ai-read-v1";

export function readReadIds(): Set<number> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) return new Set(arr.filter((x) => typeof x === "number"));
  } catch {}
  return new Set();
}

export function markRead(id: number) {
  if (typeof window === "undefined") return;
  const cur = readReadIds();
  if (cur.has(id)) return;
  cur.add(id);
  // 限制总量到最近 5000 条，避免 localStorage 撑爆
  let arr = Array.from(cur);
  if (arr.length > 5000) arr = arr.slice(-5000);
  window.localStorage.setItem(KEY, JSON.stringify(arr));
  window.dispatchEvent(new CustomEvent("read-changed", { detail: arr.length }));
}
