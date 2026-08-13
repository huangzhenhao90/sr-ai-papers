"use client";

import { track as vercelTrack } from "@vercel/analytics";

/**
 * 包装 Vercel Analytics track()，捕获异常避免影响业务。
 * 事件名约定：snake_case；props 仅传必要字段，不要传 PII。
 */
export function track(event: string, props?: Record<string, string | number | boolean | null>) {
  try {
    vercelTrack(event, props as any);
  } catch {
    // ignore — analytics 不能挂掉业务
  }
}
