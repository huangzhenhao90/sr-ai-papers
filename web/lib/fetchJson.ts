const DEFAULT_TIMEOUT_MS = 15_000;

export async function fetchJson<T>(url: string, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`${url} 请求失败（HTTP ${response.status}）`);
    }
    return await response.json() as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`${url} 请求超时`);
    }
    if (error instanceof TypeError) {
      throw new Error(`${url} 网络请求失败`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "未知错误";
}
