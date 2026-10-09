// Convert fixed UI phrases at build time, keeping OpenCC out of the runtime.
import fs from "node:fs";
import path from "node:path";
import { Converter } from "opencc-js";

const root = path.resolve(import.meta.dirname, "../src");
const source = JSON.parse(fs.readFileSync(path.join(root, "ui-source.json"), "utf8"));
const english = JSON.parse(fs.readFileSync(path.join(root, "messagesEn.json"), "utf8"));
const convert = Converter({ from: "cn", to: "hk" });
const overrides = {
  "设置": "設定", "通用设置": "通用設定", "界面语言": "介面語言",
  "简体中文": "简体中文", "繁體中文": "繁體中文",
  "保存后切换应用的显示语言。": "儲存後切換應用程式的顯示語言。",
  "保存设置": "儲存設定", "设置已保存。": "設定已儲存。",
  "任务中心": "任務中心", "投稿记录": "投稿紀錄", "数据中心": "數據中心",
  "账号与连接": "帳號與連線", "工作目录": "工作目錄",
};
// English keys also contain the dynamic fragments used by i18n.ts.
const phrases = [...new Set([...source, ...Object.keys(english)])].sort();
const translations = Object.fromEntries(phrases.map((text) => [text, overrides[text] ?? convert(text)]));
const destination = path.join(root, "messagesHk.json");
const serialized = JSON.stringify(translations, null, 2) + "\n";
if (process.argv.includes("--check")) {
  if (!fs.existsSync(destination) || fs.readFileSync(destination, "utf8").replace(/\r\n/g, "\n") !== serialized) {
    console.error("Traditional UI translations are stale. Run node scripts/build-ui-locales.mjs.");
    process.exitCode = 1;
  }
} else {
  fs.writeFileSync(destination, serialized);
}
