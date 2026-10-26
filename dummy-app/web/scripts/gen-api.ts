// npm run gen:api       writes src/api/generated/{gateway,controller}.ts
// npm run check:api     exits 1 if a committed file differs from a fresh generation
import { readFileSync, writeFileSync } from "node:fs";
import { API_NAMES, generate, generatedPath } from "./api-codegen.ts";

const check = process.argv.includes("--check");
const stale: string[] = [];

for (const name of API_NAMES) {
  const path = generatedPath(name);
  const fresh = await generate(name);
  let current: string | null = null;
  try {
    current = readFileSync(path, "utf8").replace(/\r\n/g, "\n");
  } catch {
    current = null;
  }
  if (current === fresh) continue;
  if (check) stale.push(name);
  else {
    writeFileSync(path, fresh);
    console.log(`wrote ${path}`);
  }
}

if (stale.length > 0) {
  console.error(`stale generated API types: ${stale.join(", ")} (run npm run gen:api)`);
  process.exit(1);
}
