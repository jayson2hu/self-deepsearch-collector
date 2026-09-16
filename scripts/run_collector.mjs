import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolvePython } from './run_python.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const [command, ...rest] = process.argv.slice(2);
if (!command || !['demo', 'test', 'probe', 'collect', 'validate'].includes(command)) {
  console.error('Usage: npm run collector -- <demo|test|probe|collect|validate> [arguments]');
  process.exitCode = 2;
} else {
  const python = resolvePython();
  const version = spawnSync(python, ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 2)'], {
    cwd: root, stdio: 'ignore', windowsHide: true,
  });
  if (version.error || version.status !== 0) {
    console.error('Python 3.12+ is required. Create .venv with Python 3.12+ or set PYTHON to its executable path.');
    process.exit(2);
  }
  const args = command === 'test'
    ? ['-m', 'unittest', 'discover', '-s', 'workers/collector-python/tests', ...rest]
    : command === 'demo'
      ? ['-m', 'collector.demo', ...rest]
      : ['-m', 'collector.cli', command, ...rest];
  const result = spawnSync(python, args, {
    cwd: root,
    env: { ...process.env, PYTHONPATH: path.join(root, 'workers/collector-python') },
    stdio: 'inherit',
    windowsHide: true,
  });
  if (result.error) {
    console.error('Python could not start. Create .venv or set PYTHON to its executable path.');
    process.exitCode = 1;
  } else process.exitCode = result.status ?? 1;
}
