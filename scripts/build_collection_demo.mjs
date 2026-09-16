import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolvePython } from './run_python.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'docs/collection-demo');
mkdirSync(output, { recursive: true });
const result = spawnSync(resolvePython(), ['-m', 'collector.demo', '--output-dir', output], {
  cwd: root,
  env: { ...process.env, PYTHONPATH: path.join(root, 'workers/collector-python') },
  stdio: 'inherit',
});
if (result.status !== 0) throw new Error('Collection fixture build failed');
const dataset = JSON.parse(readFileSync(path.join(output, 'dataset.json'), 'utf8'));
const safeJSON = JSON.stringify(dataset).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
const template = readFileSync(path.join(root, 'scripts/templates/collection-demo.html'), 'utf8');
if (template.split('__DATASET__').length !== 2) throw new Error('Expected exactly one data placeholder');
if (template.split('__CONFIG_SCRIPT__').length !== 2) throw new Error('Expected exactly one config placeholder');
if (template.split('__MANAGEMENT_SCRIPT__').length !== 2) throw new Error('Expected exactly one management placeholder');
const configModule = readFileSync(path.join(root, 'scripts/collection_demo_config.mjs'), 'utf8').replace(/^export /gm, '');
const configScript = `const CollectionConfig = (() => {\n${configModule}\nreturn { STORAGE_KEY, FIELDS, JOB_TYPES, createWebsite, createTaskPlan, restoreConfig, canRunFixture };\n})();`;
const management = readFileSync(path.join(root, 'scripts/templates/collection-management.js'), 'utf8');
writeFileSync(path.join(output, 'index.html'), template.replace('__DATASET__', () => safeJSON)
  .replace('__CONFIG_SCRIPT__', () => configScript).replace('__MANAGEMENT_SCRIPT__', () => management));
console.log('Built docs/collection-demo/index.html (standalone, no external assets)');
