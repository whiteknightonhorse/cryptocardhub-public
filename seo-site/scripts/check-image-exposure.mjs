// CCH-DEPS-02: guard for dismissed Dependabot #10/#47 (DEPS-FLEET-1123 ruling-2).
// Astro < 7.2.8 must never ship image optimization or a server output.
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('..', import.meta.url).pathname;
const version = JSON.parse(readFileSync(join(root, 'node_modules/astro/package.json'), 'utf8')).version;
const [maj, min, pat] = version.split('.').map((n) => parseInt(n, 10) || 0);
if (maj > 7 || (maj === 7 && (min > 2 || (min === 2 && pat >= 8)))) {
  console.log(`guard retired: astro ${version}`);
  process.exit(0);
}

const cfgName = readdirSync(root).find((f) => /^astro\.config\.(mjs|js|ts|cjs)$/.test(f));
// strip comments so a commented-out key does not trip the guard
const cfg = cfgName
  ? readFileSync(join(root, cfgName), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  : '';
const reasons = [];
const out = cfg.match(/\boutput\s*:\s*['"]?(\w+)/);
if (out && out[1] !== 'static') reasons.push(`config sets output '${out[1]}'`);
if (/\badapter\s*:/.test(cfg)) reasons.push('config names an adapter');
for (const k of ['domains', 'remotePatterns', 'service']) {
  if (new RegExp(`\\b${k}\\s*:`).test(cfg)) reasons.push(`config has image.${k}`);
}

const pat2 = /astro:assets|<Image|<Picture|getImage\(/;
const walk = (d) => {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (/\.(astro|mjs|js|ts|tsx|jsx|md|mdx)$/.test(e.name)) {
      readFileSync(p, 'utf8').split('\n').forEach((l, i) => {
        if (pat2.test(l)) reasons.push(`${p.slice(root.length)}:${i + 1} matches ${l.match(pat2)[0]}`);
      });
    }
  }
};
walk(join(root, 'src'));

for (const r of reasons) {
  console.error(`IMAGE-EXPOSURE: ${r} - astro ${version} is below 7.2.8; reopen Dependabot #10 (cryptocardhub-defycard) and #47 (cryptocardhub-public), see DEPS-FLEET-1123 ruling-2`);
}
process.exit(reasons.length ? 1 : 0);
