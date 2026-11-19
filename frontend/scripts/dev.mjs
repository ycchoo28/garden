import { spawnSync } from 'node:child_process';
import path from 'node:path';

const args = process.argv.slice(2).filter((arg) => arg !== '--strictPort');
const rsbuildBin = path.resolve(import.meta.dirname, '../node_modules/.bin/rsbuild');
const result = spawnSync(rsbuildBin, ['dev', ...args], { stdio: 'inherit' });
process.exit(result.status ?? 1);
