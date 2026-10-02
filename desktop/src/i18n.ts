import { Converter } from "opencc-js";
import source from "./ui-source.json";
import english from "./messagesEn.json";
import type { Config } from "./types";

type Language = Config["ui_language"];
type AttributeName = "aria-label" | "placeholder" | "title";
const sourcePhrases = new Set<string>(source);
const en = english as Record<string, string>;
const traditional = Converter({ from: "cn", to: "hk" });
const hk: Record<string, string> = {
  "设置": "設定", "通用设置": "通用設定", "界面语言": "介面語言",
  "简体中文": "简体中文", "繁體中文": "繁體中文",
  "保存后切换应用的显示语言。": "儲存後切換應用程式的顯示語言。",
  "保存设置": "儲存設定", "设置已保存。": "設定已儲存。",
  "任务中心": "任務中心", "投稿记录": "投稿紀錄",
  "账号与连接": "帳號與連線", "工作目录": "工作目錄",
};
const toTraditional = (text: string) => hk[text] ?? traditional(text);
const dynamicFragments = [
  "的创作中心，并确认没有遗留上传进程。恢复该账号上传队列？",
  "已停止未投稿任务并保留素材。仍有",
  "的登录凭据？任务和账号仍保留。",
  "个任务待收尾；投稿结束后自动退出。",
  "？只有全部任务已提交时才能释放名额。",
  "未匹配到可投稿分区（ID",
  "条记录，跳过", "条已有记录。", "条投稿记录。", "个任务正在队列中",
  "个在途投稿、", "未连接到后台：", "确认投稿到", "当前可用空间",
  "· 已切换服务（", "· 作品 ID：", "请先核对", "· 剩余", "可用空间",
  "清除", "已导出", "导入", "已登录", "需重新扫码", "需重新授权",
];
const textSource = new WeakMap<Text, { original: string; rendered: string }>();
const attributeSource = new WeakMap<Element, Map<AttributeName, { original: string; rendered: string }>>();
let language: Language = "zh-CN";

const normal = (value: string) => value.replace(/\s+/g, " ").trim();

function translate(value: string): string {
  const key = normal(value);
  if (!key) return value;
  let translated = key;
  if (sourcePhrases.has(key)) {
    translated = language === "en" ? (en[key] ?? key) : language === "zh-HK" ? toTraditional(key) : key;
  } else if (language !== "zh-CN" && dynamicFragments.some((fragment) => key.includes(fragment))) {
    for (const fragment of dynamicFragments) {
      if (key.includes(fragment)) {
        const replacement = language === "en" ? en[fragment] : toTraditional(fragment);
        if (replacement) translated = translated.replaceAll(fragment, replacement);
      }
    }
  }
  if (!translated || translated === key) return value;
  const start = value.match(/^\s*/)?.[0] ?? "";
  const end = value.match(/\s*$/)?.[0] ?? "";
  return start + translated + end;
}

export const uiText = translate;

function translateText(node: Text) {
  const current = node.data;
  const saved = textSource.get(node);
  const original = saved && current === saved.rendered ? saved.original : current;
  const rendered = translate(original);
  textSource.set(node, { original, rendered });
  if (current !== rendered) node.data = rendered;
}

function translateAttributes(element: Element) {
  const saved = attributeSource.get(element) ?? new Map();
  for (const name of ["aria-label", "placeholder", "title"] as const) {
    const current = element.getAttribute(name);
    if (current == null) continue;
    const previous = saved.get(name);
    const original = previous && current === previous.rendered ? previous.original : current;
    const rendered = translate(original);
    saved.set(name, { original, rendered });
    if (current !== rendered) element.setAttribute(name, rendered);
  }
  attributeSource.set(element, saved);
}

function translateTree(root: Node) {
  if (root.nodeType === Node.TEXT_NODE) {
    translateText(root as Text);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  const element = root as Element;
  if (element.closest("[data-no-localize], [contenteditable=true], script, style")) return;
  translateAttributes(element);
  for (const child of element.childNodes) translateTree(child);
}

export function setUiLanguage(value: Language) {
  language = value;
  document.documentElement.lang = value;
  try { localStorage.setItem("yt2bili.uiLanguage", value); } catch { /* Backend setting remains authoritative. */ }
  translateTree(document.body);
}

export function startUiLocalization() {
  try {
    const cached = localStorage.getItem("yt2bili.uiLanguage");
    if (cached === "zh-CN" || cached === "zh-HK" || cached === "en") language = cached;
  } catch { /* Use the default until settings load. */ }
  document.documentElement.lang = language;
  const observer = new MutationObserver((changes) => {
    for (const change of changes) {
      if (change.type === "characterData") translateText(change.target as Text);
      else if (change.type === "attributes") translateAttributes(change.target as Element);
      else for (const node of change.addedNodes) translateTree(node);
    }
  });
  observer.observe(document.body, { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ["aria-label", "placeholder", "title"] });
  translateTree(document.body);
  return () => observer.disconnect();
}
