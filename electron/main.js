const { app, BrowserWindow, session, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const http = require('http');

let mainWindow = null;
let pythonProcess = null;

const isDev = !app.isPackaged;
const BACKEND_PORT = 8765;
const BACKEND_URL = `http://localhost:${BACKEND_PORT}`;
const HEALTH_ENDPOINT = `${BACKEND_URL}/api/health`;
const HEALTH_POLL_INTERVAL = 500;
const HEALTH_POLL_TIMEOUT = 30000;

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
function getBackendDir() {
  if (isDev) {
    return path.join(__dirname, '..', 'backend');
  }
  return path.join(process.resourcesPath, 'backend');
}

function getPythonExecutable() {
  const backendDir = getBackendDir();
  const ext = process.platform === 'win32' ? '.exe' : '';
  return path.join(backendDir, `backend${ext}`);
}

// 初始化配置文件（如果不存在）
function initConfig() {
  const backendDir = getBackendDir();
  const configPath = path.join(backendDir, 'config.yaml');
  const examplePath = path.join(backendDir, 'config.yaml.example');

  if (!fs.existsSync(configPath) && fs.existsSync(examplePath)) {
    console.log('[main] Creating config.yaml from example...');
    fs.copyFileSync(examplePath, configPath);
  }
}

function startPythonBackend() {
  const executable = getPythonExecutable();
  const backendDir = getBackendDir();
  const dataDir = app.getPath('userData');

  // 确保数据目录存在
  if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true });
  }

  console.log(`[main] Starting backend: ${executable} (dir: ${backendDir}, data: ${dataDir})`);

  const options = {
    cwd: backendDir,
    stdio: ['pipe', 'pipe', 'pipe'],
    shell: process.platform === 'win32',
    env: {
      ...process.env,
      DENTAL_AGENT_DATA_DIR: dataDir,
      DENTAL_AGENT_DEV: isDev ? '1' : '0',
    },
  };

  pythonProcess = spawn(executable, [], options);

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
        res.resume();
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
    show: false, // 先隐藏窗口，等加载完成后再显示
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
    // 生产模式：加载打包后的前端文件
    const indexPath = path.join(__dirname, '..', 'frontend', 'dist', 'index.html');
    console.log('[main] Loading index.html from:', indexPath);
    mainWindow.loadFile(indexPath);
  }

  // 窗口加载完成后显示
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // 加载失败时显示错误
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error('[main] Failed to load:', errorCode, errorDescription);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------
app.whenReady().then(async () => {
  setDefaultCSP();

  // 初始化配置文件
  initConfig();

  // Start Python backend and wait for it to be ready
  startPythonBackend();

  try {
    await pollHealth();
    console.log('[main] Backend is ready.');
  } catch (err) {
    console.error('[main] Backend failed to start:', err.message);
    dialog.showErrorBox(
      '启动失败',
      `后端服务启动失败：${err.message}\n\n请检查是否有其他程序占用了端口 ${BACKEND_PORT}`
    );
    app.quit();
    return;
  }

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  killPythonBackend();
});

app.on('quit', () => {
  killPythonBackend();
});
