import { useSyncExternalStore, type ReactNode } from "react";
import english from "./messagesEn.json";
import traditional from "./messagesHk.json";
import type { Config } from "./types";

type Language = Config["ui_language"];
type Parameters = Record<string, string | number | boolean | null | undefined>;
const en = english as Record<string, string>;
const hk = traditional as Record<string, string>;
let language: Language = "zh-CN";
const listeners = new Set<() => void>();
const normal = (value: string) => value.replace(/\s+/g, " ").trim();
const escapePattern = (value: string) =>
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const parameterPattern = /\{(\w+)\}/g;

// Match complete application messages, never arbitrary fragments of user content.
// Parameters (account names, paths, service details) are interpolated verbatim.
const templates = Object.keys(en)
  .filter((key) => /\{\w+\}/.test(key))
  .map((key) => {
    const names: string[] = [];
    let cursor = 0;
    let pattern = "^";
    for (const match of key.matchAll(parameterPattern)) {
      pattern +=
        escapePattern(key.slice(cursor, match.index)).replace(/\s+/g, "\\s+") +
        "([\\s\\S]*?)";
      names.push(match[1]);
      cursor = match.index! + match[0].length;
    }
    pattern += escapePattern(key.slice(cursor)).replace(/\s+/g, "\\s+") + "$";
    return {
      key,
      names,
      pattern: new RegExp(pattern),
      specificity: key.replace(parameterPattern, "").length,
    };
  })
  .sort((a, b) => b.specificity - a.specificity);

function interpolate(text: string, params: Parameters) {
  return text.replace(parameterPattern, (placeholder, name: string) =>
    Object.prototype.hasOwnProperty.call(params, name)
      ? String(params[name] ?? "")
      : placeholder,
  );
}

export function uiText(value: string, params?: Parameters): string {
  if (!value) return value;
  const key = normal(value);
  const dictionary =
    language === "en" ? en : language === "zh-HK" ? hk : undefined;
  let translated = dictionary?.[key];
  if (params)
    return translated
      ? (value.match(/^\s*/)?.[0] ?? "") +
          interpolate(translated, params) +
          (value.match(/\s*$/)?.[0] ?? "")
      : interpolate(value, params);
  if (!translated && dictionary) {
    for (const template of templates) {
      const match = value.trim().match(template.pattern);
      if (!match) continue;
      const values = Object.fromEntries(
        template.names.map((name, index) => [name, match[index + 1]]),
      );
      translated = interpolate(
        dictionary[template.key] ?? template.key,
        values,
      );
      break;
    }
  }
  if (!translated && dictionary && value.startsWith("Error: "))
    return "Error: " + uiText(value.slice(7));
  if (!translated) return value;
  return (
    (value.match(/^\s*/)?.[0] ?? "") +
    translated +
    (value.match(/\s*$/)?.[0] ?? "")
  );
}

/** Localize string results at the React render boundary, preserving other nodes. */
export function uiNode(value: ReactNode): ReactNode {
  if (typeof value === "string") return uiText(value);
  if (Array.isArray(value)) return value.map(uiNode);
  return value;
}

export function confirmUi(message: string) {
  return window.confirm(uiText(message));
}

export const getUiLanguage = () => language;
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
export const useUiLanguage = () =>
  useSyncExternalStore(subscribe, getUiLanguage, getUiLanguage);

export function setUiLanguage(value: Language) {
  document.documentElement.lang = value;
  try {
    localStorage.setItem("screator.uiLanguage", value);
  } catch {
    /* Backend settings remain authoritative. */
  }
  if (language === value) return;
  language = value;
  listeners.forEach((listener) => listener());
}

export function startUiLocalization() {
  try {
    const cached = localStorage.getItem("screator.uiLanguage");
    if (cached === "zh-CN" || cached === "zh-HK" || cached === "en")
      language = cached;
  } catch {
    /* Use the default until settings load. */
  }
  document.documentElement.lang = language;
}

export const formatDateTime = (
  value: Date | string | number,
  options?: Intl.DateTimeFormatOptions,
) => new Date(value).toLocaleString(language, options);
export const formatTime = (value: Date | string | number) =>
  new Date(value).toLocaleTimeString(language);
export const formatNumber = (value: number) => value.toLocaleString(language);
