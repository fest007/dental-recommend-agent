const { app, BrowserWindow, session } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

let mainWindow = null;
let pythonProcess = null;

const isDev = !app.isPackaged;
const BACKEND_URL = 'http://localhost:8765';
const HEALTH_ENDPOINT = `${BACKEND_URL}/api/health`;
const HEALTH_POLL_INTERVAL = 500; // ms
const HEALTH_POLL_TIMEOUT = 30000; // ms

// ---------------------------------------------------------------------------
// Content-Security-Policy
// ---------------------------------------------------------------------------
function setDefaultCSP() {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          isDev
            ? "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self' http://localhost:* ws://localhost:*; img-src 'self' data:; font-src 'self' data:;"
            : "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' http://localhost:*; img-src 'self' data:; font-src 'self' data:;"
        ],
      },
    });
  });
}

// ---------------------------------------------------------------------------
// Python backend management
// ---------------------------------------------------------------------------
function getPythonExecutable() {
  if (isDev) {
    return process.platform === 'win32' ? 'python' : 'python3';
  }
  // PyInstaller one-dir output: resources/backend/backend(.exe)
  const ext = process.platform === 'win32' ? '.exe' : '';
  return path.join(process.resourcesPath, 'backend', `backend${ext}`);
}

function getBackendCwd() {
  if (isDev) {
    return path.join(__dirname, '..', 'backend');
  }
  // data/ and config.yaml live at resourcesPath level
  return process.resourcesPath;
}

function startPythonBackend() {
  const executable = getPythonExecutable();
  const cwd = getBackendCwd();
  const dataDir = app.getPath('userData');

  console.log(`[main] Starting backend: ${executable} main.py (cwd: ${cwd}, data: ${dataDir})`);

  const options = {
    cwd,
    stdio: ['pipe', 'pipe', 'pipe'],
    shell: process.platform === 'win32',
    env: {
      ...process.env,
      DENTAL_AGENT_DATA_DIR: dataDir,
      DENTAL_AGENT_DEV: isDev ? '1' : '0',
    },
  };

  if (isDev) {
    pythonProcess = spawn(executable, ['main.py'], options);
  } else {
    pythonProcess = spawn(executable, [], options);
  }

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[backend:stdout] ${data.toString().trim()}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[backend:stderr] ${data.toString().trim()}`);
  });

  pythonProcess.on('error', (err) => {
    console.error('[main] Failed to start backend process:', err);
  });

  pythonProcess.on('exit', (code, signal) => {
    console.log(`[main] Backend exited with code ${code}, signal ${signal}`);
    pythonProcess = null;
  });
}

function killPythonBackend() {
  if (!pythonProcess) return;
  console.log('[main] Killing backend process...');
  try {
    pythonProcess.kill('SIGTERM');
    // Force kill after 3 seconds if still alive
    setTimeout(() => {
      if (pythonProcess) {
        console.log('[main] Force-killing backend process...');
        pythonProcess.kill('SIGKILL');
      }
    }, 3000);
  } catch (err) {
    console.error('[main] Error killing backend:', err);
  }
}

// ---------------------------------------------------------------------------
// Health polling
// ---------------------------------------------------------------------------
function pollHealth() {
  return new Promise((resolve, reject) => {
    const startTime = Date.now();

    function check() {
      const req = http.get(HEALTH_ENDPOINT, (res) => {
        if (res.statusCode === 200) {
          console.log('[main] Backend health check passed.');
          resolve();
        } else {
          retry();
        }
        res.resume(); // consume response
      });

      req.on('error', () => retry());
      req.setTimeout(2000, () => {
        req.destroy();
        retry();
      });
    }

    function retry() {
      if (Date.now() - startTime > HEALTH_POLL_TIMEOUT) {
        reject(new Error('Backend health check timed out'));
        return;
      }
      setTimeout(check, HEALTH_POLL_INTERVAL);
    }

    check();
  });
}

// ---------------------------------------------------------------------------
// BrowserWindow
// ---------------------------------------------------------------------------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    title: '牙科设备推荐Agent',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'frontend', 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------
app.whenReady().then(async () => {
  setDefaultCSP();

  // Start Python backend and wait for it to be ready
  startPythonBackend();

  try {
    await pollHealth();
    console.log('[main] Backend is ready.');
  } catch (err) {
    console.error('[main] Backend failed to start:', err.message);
    // Still open the window so the user sees something
  }

  createWindow();

  app.on('activate', () => {
    // macOS: re-create window when dock icon is clicked and no windows exist
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

// Quit when all windows are closed (except on macOS)
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// Clean up backend process before quitting
app.on('before-quit', () => {
  killPythonBackend();
});

app.on('quit', () => {
  killPythonBackend();
});
