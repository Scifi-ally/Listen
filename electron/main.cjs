const { app, BrowserWindow, ipcMain, shell } = require('electron');
const { spawn, spawnSync } = require('node:child_process');
const { existsSync } = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const ROOT = path.resolve(__dirname, '..');
const PORT = Number(process.env.LISTEN_PORT || 8765);
const PYTHON = process.env.LISTEN_PYTHON || path.join(ROOT, '.venv', 'bin', 'python');
let backend = null;
let windowRef = null;
let ollamaBootstrap = null;

function waitForBackend(url, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const attempt = () => {
      const request = http.get(url, (response) => {
        response.resume();
        if (response.statusCode && response.statusCode < 500) return resolve();
        retry();
      });
      request.on('error', retry);
      request.setTimeout(1000, () => request.destroy());
    };
    const retry = () => {
      if (Date.now() - started > timeoutMs) return reject(new Error(`Listen backend did not start on port ${PORT}`));
      setTimeout(attempt, 250);
    };
    attempt();
  });
}

function startLocalOllama() {
  if (!existsSync(PYTHON)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    ollamaBootstrap = spawn(PYTHON, ['-m', 'listen_app.ollama_setup', '--start-only'], {
      cwd: ROOT,
      env: { ...process.env, PYTHONPATH: ROOT },
      stdio: 'ignore',
      detached: false,
    });
    ollamaBootstrap.once('error', reject);
    ollamaBootstrap.once('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Local Ollama startup failed with code ${code}`));
    });
  });
}

async function startBackend() {
  if (!existsSync(PYTHON)) {
    throw new Error('Local Python environment is missing. Run ./run-desktop.sh once to install it.');
  }
  await startLocalOllama();
  backend = spawn(PYTHON, ['-m', 'uvicorn', 'listen_app.main:app', '--host', '127.0.0.1', '--port', String(PORT)], {
    cwd: ROOT,
    env: {
      ...process.env,
      PYTHONPATH: ROOT,
      LISTEN_DEFAULT_ASR_MODEL: process.env.LISTEN_DEFAULT_ASR_MODEL || 'auto',
    },
    stdio: 'pipe',
  });
  backend.stderr.on('data', (chunk) => {
    if (process.env.LISTEN_DEBUG) process.stderr.write(chunk);
  });
  backend.on('exit', (code) => {
    if (code && windowRef && !windowRef.isDestroyed()) {
      windowRef.webContents.send('backend-error', `Local backend exited with code ${code}`);
    }
  });
  return waitForBackend(`http://127.0.0.1:${PORT}/api/health`);
}

function createWindow() {
  windowRef = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 620,
    frame: false,
    titleBarStyle: 'hidden',
    backgroundColor: '#050505',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  windowRef.removeMenu();
  windowRef.loadURL(`http://127.0.0.1:${PORT}`);
  windowRef.once('ready-to-show', () => windowRef.show());
  windowRef.on('closed', () => { windowRef = null; });
}

ipcMain.on('window:minimize', () => windowRef?.minimize());
ipcMain.on('window:maximize', () => {
  if (windowRef?.isMaximized()) windowRef.unmaximize();
  else windowRef?.maximize();
});
ipcMain.on('window:close', () => windowRef?.close());
ipcMain.handle('open-external', (_event, url) => {
  if (typeof url === 'string' && url.startsWith('http://127.0.0.1')) return shell.openExternal(url);
  return false;
});

app.whenReady().then(async () => {
  try {
    await startBackend();
    createWindow();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    app.exit(1);
    console.error(message);
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (backend && !backend.killed) backend.kill('SIGTERM');
  if (ollamaBootstrap && !ollamaBootstrap.killed) ollamaBootstrap.kill('SIGTERM');
});
