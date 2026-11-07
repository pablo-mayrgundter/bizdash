// Shared by the Node unit tests: the packaged SPA's modules and the demo config.
import fs from "node:fs";
import { fileURLToPath } from "node:url";

export const WEB = new URL("../../bizdash/web/", import.meta.url);
export const src = path => new URL(`src/${path}`, WEB).href;
export const demoConfigRaw = () => JSON.parse(fs.readFileSync(fileURLToPath(new URL("bizdash.config.json", WEB))));
// A fixed "now" so every run sees the same synthetic data: 2026-11-04 15:30 UTC.
export const NOW = Date.UTC(2026, 10, 4, 15, 30);
