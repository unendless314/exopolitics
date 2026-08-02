import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Committed development fixture export root. Never used implicitly: this
// wrapper is the only entry point that sets SITE_PUBLISH_EXPORT_DIR to it.
const fixtureRoot = path.resolve(__dirname, '..', 'tests', 'fixtures', 'publish_export');

// Command-string form with shell:true: resolves npm/npm.cmd on all platforms
// without hitting Windows' no-shell batch-file spawn restriction (EINVAL).
const child = spawn('npm run dev', {
  env: { ...process.env, SITE_PUBLISH_EXPORT_DIR: fixtureRoot },
  stdio: 'inherit',
  shell: true,
});

// Forward termination signals to the child so Ctrl+C does not orphan the
// dev server.
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    child.kill(signal);
  });
}

// Last-resort orphan guard (e.g. an outer npm wrapper being killed first).
process.on('exit', () => {
  try {
    child.kill();
  } catch {
    // Child already gone.
  }
});

child.on('error', (err) => {
  console.error(`Failed to start dev server: ${err.message}`);
  process.exit(1);
});

child.on('exit', (code, signal) => {
  // Preserve the child's outcome: exit code verbatim, or the conventional
  // 128+n code when the child died from a signal.
  if (signal === 'SIGINT') process.exit(130);
  if (signal === 'SIGTERM') process.exit(143);
  process.exit(code ?? 1);
});
