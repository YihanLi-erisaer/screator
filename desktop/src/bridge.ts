import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Image as TauriImage } from "@tauri-apps/api/image";
import { open, save } from "@tauri-apps/plugin-dialog";
import { openUrl } from "@tauri-apps/plugin-opener";

export const preview =
  import.meta.env.DEV &&
  !isTauri() &&
  new URLSearchParams(location.search).has("preview");
let shuttingDown = false;
export function beginShutdown() { shuttingDown = true; }
export async function setWindowTheme(theme: "system" | "dark" | "light") {
  if (isTauri())
    await getCurrentWindow().setTheme(theme === "system" ? null : theme);
}
export async function setWindowIcon(theme: "dark" | "light") {
  if (!isTauri()) return;
  const response = await fetch(theme === "dark" ? "/brand-dark.png" : "/brand.png");
  if (!response.ok) throw new Error(`Icon request failed: ${response.status}`);
  const bitmap = await createImageBitmap(await response.blob());
  try {
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Cannot create icon canvas");
    context.drawImage(bitmap, 0, 0);
    const rgba = new Uint8Array(
      context.getImageData(0, 0, bitmap.width, bitmap.height).data,
    );
    const icon = await TauriImage.new(rgba, bitmap.width, bitmap.height);
    try {
      await getCurrentWindow().setIcon(icon);
    } finally {
      await icon.close();
    }
  } finally {
    bitmap.close();
  }
}
export async function request<T = any>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  if (shuttingDown && !method.startsWith("system."))
    throw new Error("应用正在退出。");
  if (shuttingDown && !["system.prepare_shutdown", "system.shutdown_status"].includes(method))
    throw new Error("应用正在退出。");
  if (preview) return (await import("./preview")).request(method, params) as T;
  if (!isTauri())
    throw new Error(
      "请使用 npm run desktop 启动桌面应用。此页面尚未连接本地后台。",
    );
  const result = await invoke<T>("backend_request", { method, params });
  if (method === "system.health") {
    await invoke("frontend_ready", { health: result });
  }
  return result;
}
export async function subscribe(
  callback: (event: { event: string; payload: any }) => void,
) {
  if (!isTauri()) return () => {};
  const stop = await listen<{ event: string; payload: any }>(
    "backend-event",
    (e) => callback(e.payload),
  );
  const offline = await listen("backend-disconnected", () =>
    callback({ event: "disconnected", payload: {} }),
  );
  return () => {
    stop();
    offline();
  };
}
export async function onClose(callback: () => void) {
  return isTauri() ? listen("app-close-requested", callback) : () => {};
}
export const closeApp = () => invoke("finish_close");
export const forceCloseApp = () => invoke("force_close");
export async function chooseFile(extensions = ["txt"]) {
  if (!isTauri()) throw new Error("文件选择需要在桌面应用中使用。");
  return open({
    multiple: false,
    filters: [{ name: "支持的文件", extensions }],
  }) as Promise<string | null>;
}
export async function chooseDirectory() {
  if (!isTauri()) throw new Error("目录选择需要在桌面应用中使用。");
  return open({ directory: true, multiple: false }) as Promise<string | null>;
}
export async function saveLog() {
  return save({
    defaultPath: "Screator-diagnostics.txt",
    filters: [{ name: "诊断日志", extensions: ["txt"] }],
  });
}
export async function saveHistory() {
  if (!isTauri()) throw new Error("投稿记录导出需要在桌面应用中使用。");
  return save({
    defaultPath: `Screator-投稿记录-${new Date().toISOString().slice(0, 10)}.json`,
    filters: [{ name: "投稿记录 JSON", extensions: ["json"] }],
  });
}
export async function external(url: string) {
  if (
    !/^https:\/\/(www\.bilibili\.com|member\.bilibili\.com|member\.acfun\.cn|stardazz-com\.vercel\.app)\//.test(
      url,
    )
  )
    return;
  if (isTauri()) await openUrl(url);
  else window.open(url, "_blank", "noopener,noreferrer");
}
export const operationId = () => crypto.randomUUID();
