// Update the list of UI source phrases after changing desktop copy.
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "../src");
const phrases = new Set();
const unlocalized = [];
for (const file of fs
  .readdirSync(root)
  .filter(
    (name) =>
      /\.(ts|tsx)$/.test(name) && !["i18n.ts", "preview.ts"].includes(name),
  )) {
  const source = fs.readFileSync(path.join(root, file), "utf8");
  const tree = ts.createSourceFile(
    file,
    source,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith("tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const protectedContent = (node) => {
    for (let current = node.parent; current; current = current.parent) {
      if (
        ts.isJsxElement(current) &&
        current.openingElement.attributes.properties.some(
          (attribute) =>
            ts.isJsxAttribute(attribute) &&
            attribute.name.getText(tree) === "data-no-localize",
        )
      )
        return true;
    }
    return false;
  };
  const visit = (node) => {
    if (
      !protectedContent(node) &&
      ((ts.isJsxText(node) && /[\u3400-\u9fff]/u.test(node.text)) ||
        (ts.isJsxAttribute(node) &&
          ["aria-label", "placeholder", "title", "alt"].includes(
            node.name.getText(tree),
          ) &&
          node.initializer &&
          ts.isStringLiteral(node.initializer) &&
          /[\u3400-\u9fff]/u.test(node.initializer.text)) ||
        (ts.isCallExpression(node) &&
          ["confirm", "window.confirm"].includes(
            node.expression.getText(tree),
          )))
    ) {
      unlocalized.push(
        `${file}:${tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1}`,
      );
    }
    let text = "";
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    )
      text = node.text;
    else if (ts.isJsxText(node)) text = node.text;
    text = text.replace(/\s+/g, " ").trim();
    if (/[\u3400-\u9fff]/u.test(text)) phrases.add(text);
    ts.forEachChild(node, visit);
  };
  visit(tree);
}
const backend = JSON.parse(
  fs.readFileSync(path.join(root, "backend-source.json"), "utf8"),
);
const destination = path.join(root, "ui-source.json");
const serialized = JSON.stringify([...phrases].sort(), null, 2) + "\n";
if (process.argv.includes("--check")) {
  const english = JSON.parse(
    fs.readFileSync(path.join(root, "messagesEn.json"), "utf8"),
  );
  const required = [...new Set([...phrases, ...backend])];
  const missing = required.filter((phrase) => !english[phrase]);
  const placeholders = (value) =>
    [...value.matchAll(/\{(\w+)\}/g)]
      .map((match) => match[1])
      .sort()
      .join(",");
  const invalid = required.filter(
    (phrase) =>
      english[phrase] && placeholders(phrase) !== placeholders(english[phrase]),
  );
  if (
    fs.readFileSync(destination, "utf8").replace(/\r\n/g, "\n") !==
      serialized ||
    missing.length ||
    invalid.length ||
    unlocalized.length
  ) {
    console.error(
      "UI catalog is stale, translations are missing, or parameters differ:",
      { missing, invalid, unlocalized },
    );
    process.exitCode = 1;
  }
} else {
  fs.writeFileSync(destination, serialized);
}
