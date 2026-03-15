const { spawn } = require('child_process');
const path = require('path');

const projectDir = path.join(__dirname, '..');
const args = ['app.py'];
const opts = { stdio: 'inherit', cwd: projectDir, shell: true };

function run(cmd) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, opts);
    child.on('error', reject);
    child.on('exit', (code, signal) => resolve(code ?? (signal ? 1 : 0)));
  });
}

(async () => {
  const isWindows = process.platform === 'win32';
  try {
    const code = await run('python');
    process.exit(code);
  } catch (e) {
    if (isWindows && e.code === 'ENOENT') {
      try {
        const code = await run('py');
        process.exit(code);
      } catch (e2) {
        console.error('Python not found. Install Python and ensure "python" or "py" is in PATH.');
        process.exit(1);
      }
    } else {
      console.error('Failed to start Python:', e.message);
      process.exit(1);
    }
  }
})();
