import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { join, relative } from 'node:path';

const [root, output] = process.argv.slice(2);
function walk(path) {
  return statSync(path).isDirectory()
    ? readdirSync(path).sort().flatMap(name => walk(join(path, name)))
    : [path];
}
const selected = readdirSync(root).filter(name =>
  ['src', 'public', 'index.html', 'vite.config.ts'].includes(name)
  || /^(package.*|tsconfig.*)\.json$/.test(name));
function hashes(paths) {
  return Object.fromEntries(paths.flatMap(path => walk(join(root, path))).sort().map(path =>
    ['ui/' + relative(root, path).replaceAll('\\', '/'), createHash('sha256').update(readFileSync(path)).digest('hex')]));
}
writeFileSync(output, JSON.stringify({ schema_version: 1, kind: 'ui-build-inputs',
  inputs: hashes(selected), outputs: hashes(['dist']) }, null, 2) + '\n');
