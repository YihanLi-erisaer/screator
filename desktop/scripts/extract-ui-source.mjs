// Update the list of UI source phrases after changing desktop copy.
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "../src");
const phrases = new Set();
for (const file of fs.readdirSync(root).filter((name) => /\.(ts|tsx)$/.test(name) && !["i18n.ts", "preview.ts"].includes(name))) {
  const source = fs.readFileSync(path.join(root, file), "utf8");
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true,
    file.endsWith("tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const visit = (node) => {
    let text = "";
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) ||
        ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) text = node.text;
    else if (ts.isJsxText(node)) text = node.text;
    text = text.replace(/\s+/g, " ").trim();
    if (/[\u3400-\u9fff]/u.test(text)) phrases.add(text);
    ts.forEachChild(node, visit);
  };
  visit(tree);
}
const destination = path.join(root, "ui-source.json");
const serialized = JSON.stringify([...phrases].sort(), null, 2) + "\n";
if (process.argv.includes("--check")) {
  const english = JSON.parse(fs.readFileSync(path.join(root, "messagesEn.json"), "utf8"));
  const missing = [...phrases].filter((phrase) => !english[phrase]);
  if (fs.readFileSync(destination, "utf8") !== serialized || missing.length) {
    console.error("UI source list is stale or English translations are missing:", missing);
    process.exitCode = 1;
  }
} else {
  fs.writeFileSync(destination, serialized);
}
