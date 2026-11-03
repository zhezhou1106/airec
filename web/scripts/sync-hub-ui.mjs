#!/usr/bin/env node
/**
 * Copy MyHome's design layer into src/hub/ so AI Weekly looks like a native hub page:
 * tokens + styles (index.css, styles/catppuccin.css), the shared components (ui.tsx), the app page frame
 * (kit/appshell.tsx: header, status chip, tabs), the report renderer (kit/report.tsx) and cx/time helpers.
 * The copies are committed, so building never needs the hub checkout. Re-run after the hub's design changes:
 *   HUB_WEB=~/Desktop/home/my-hub/web npm run sync-hub-ui
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const hub = resolve(process.env.HUB_WEB ?? join(homedir(), "Desktop/home/my-hub/web"), "src");
const out = resolve(here, "../src/hub");
mkdirSync(join(out, "styles"), { recursive: true });

const banner = (from) => `/* Vendored from MyHome (${from}) by scripts/sync-hub-ui.mjs. Don't edit here. */\n`;
const copy = (from, to, fix = (s) => s) => {
  const text = readFileSync(join(hub, from), "utf8");
  writeFileSync(join(out, to), (to.endsWith(".css") ? banner(from) : banner(from).replace("/*", "//").replace(" */", "")) + fix(text));
  console.log(`${from} -> src/hub/${to}`);
};

copy("index.css", "index.css");
copy("styles/catppuccin.css", "styles/catppuccin.css");
copy("lib/format.ts", "format.ts");
copy("kit/appshell.tsx", "appshell.tsx");
copy("kit/report.tsx", "report.tsx"); // the hub's report/digest renderer (self-contained)
copy("components/ui.tsx", "ui.tsx", (s) => s.replace('from "../lib/format"', 'from "./format"').replace('from "../lib/types"', 'from "./types"'));
// ui.tsx needs only the Status type.
const types = readFileSync(join(hub, "lib/types.ts"), "utf8").match(/export type Status = [^;]+;/)?.[0];
writeFileSync(join(out, "types.ts"), `// Vendored from MyHome (lib/types.ts) by scripts/sync-hub-ui.mjs.\n${types}\n`);
console.log("lib/types.ts (Status) -> src/hub/types.ts");
