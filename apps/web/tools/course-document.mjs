import fs from "node:fs";
import { isAlias, isCollection, isMap, isScalar, isSeq, parseDocument, visit } from "yaml";

// Matches Core's bounded UniqueKeySafeLoader policy for ordinary YAML/JSON.
export const MAX_DOCUMENT_BYTES = 256 * 1024;
export const MAX_DOCUMENT_DEPTH = 32;
const isMergeKey = (node) => isScalar(node) && (node.tag === "tag:yaml.org,2002:merge" || typeof node.value === "symbol" && node.value.description === "<<");

function effectiveKeys(map) {
  const keys = new Set();
  const add = (key) => {
    if (keys.has(key)) throw new Error("Duplicate YAML key after merge");
    keys.add(key);
  };
  for (const pair of map.items) {
    if (!isMergeKey(pair.key)) { add(pair.key.value); continue; }
    const sources = isSeq(pair.value) ? pair.value.items : [pair.value];
    for (const source of sources) {
      if (!isMap(source)) throw new Error("YAML merge source must be a map");
      for (const key of effectiveKeys(source)) add(key);
    }
  }
  return keys;
}

export function parseCourseDocument(text) {
  if (Buffer.byteLength(text, "utf8") > MAX_DOCUMENT_BYTES) throw new Error("Document exceeds 262144 bytes");
  const document = parseDocument(text, { schema: "yaml-1.1", uniqueKeys: true, strict: true });
  if (document.errors.length || document.warnings.length) throw new Error("Invalid YAML/JSON document", { cause: document.errors[0] ?? document.warnings[0] });
  visit(document, (key, node, ancestors) => {
    if (isAlias(node)) throw new Error("YAML aliases are not permitted");
    const depth = ancestors.filter(isCollection).length;
    if (depth > MAX_DOCUMENT_DEPTH) throw new Error("Document nesting exceeds 32");
    if (key === "key" && !isMergeKey(node) && (!isScalar(node) || typeof node.value !== "string")) throw new Error("Document keys must be strings");
  });
  if (!isMap(document.contents)) throw new Error("Document root must be an object");
  visit(document, { Map: (_key, node) => { effectiveKeys(node); } });
  return document.toJS({ maxAliasCount: 0 });
}

export function readCourseDocument(file) {
  const fd = fs.openSync(file, "r");
  try {
    const bytes = Buffer.alloc(MAX_DOCUMENT_BYTES + 1);
    const size = fs.readSync(fd, bytes, 0, bytes.length, 0);
    if (size > MAX_DOCUMENT_BYTES) throw new Error("Document exceeds 262144 bytes");
    return parseCourseDocument(new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, size)));
  } finally { fs.closeSync(fd); }
}
