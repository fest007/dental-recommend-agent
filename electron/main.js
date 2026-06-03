const { app, BrowserWindow, session, dialog, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const http = require('http');
const yaml = require('js-yaml');

let mainWindow = null;
let pythonProcess = null;
let backendReady = false;
let backendPort = 8765;
let startupStatus = 'initializing';

const isDev = !app.isPackaged;

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
            ? "default-src 'self' http://localhost:*; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self' http://localhost:* ws://localhost:*; img-src 'self' data:; font-src 'self' data:;"
            : "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' http://localhost:*; img-src 'self' data:; font-src 'self' data:;"
        ],
      },
    });
  });
}

// ---------------------------------------------------------------------------
// 路径管理
// ---------------------------------------------------------------------------
function getBackendSourceDir() {
  if (isDev) {
    return path.join(__dirname, '..', 'backend');
  }
  return path.join(process.resourcesPath, 'backend');
}

function getBackendDataDir() {
  const dataDir = path.join(app.getPath('userData'), 'backend-data');
  if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true });
  }
  return dataDir;
}

function getPythonExecutable() {
  const backendDir = getBackendSourceDir();
  const ext = process.platform === 'win32' ? '.exe' : '';
  return path.join(backendDir, `backend${ext}`);
}

function getConfigPath() {
  return path.join(getBackendDataDir(), 'config.yaml');
}

function getPortInfoPath() {
  return path.join(getBackendDataDir(), 'port.json');
}

// ---------------------------------------------------------------------------
// 配置文件管理
// ---------------------------------------------------------------------------
function loadConfig() {
  const configPath = getConfigPath();
  const sourceDir = getBackendSourceDir();
  const examplePath = path.join(sourceDir, 'config.yaml.example');

  if (!fs.existsSync(configPath)) {
    if (fs.existsSync(examplePath)) {
      console.log('[main] Creating config.yaml from example...');
      const content = fs.readFileSync(examplePath, 'utf-8');
      fs.writeFileSync(configPath, content, 'utf-8');
    } else {
      console.log('[main] Creating default config.yaml...');
      const defaultConfig = `llm:
  base_url: "https://api.openai.com/v1"
  api_key: ""
  ranking_model: "gpt-4o"
  enrichment_model: "gpt-4o-mini"
  embedding_model: "text-embedding-3-small"
  temperature: 0.7
  max_tokens: 4096
  timeout: 30

server:
  host: "127.0.0.1"
  port: 8765

database:
  path: "app.db"

qdrant:
  path: "qdrant"
  collection: "products"
`;
      fs.writeFileSync(configPath, defaultConfig, 'utf-8');
    }
  }

  try {
    const configContent = fs.readFileSync(configPath, 'utf-8');
    return yaml.load(configContent) || {};
  } catch (err) {
    console.error('[main] Failed to load config:', err);
    return {};
  }
}

// ---------------------------------------------------------------------------
// 清理旧的端口文件
// ---------------------------------------------------------------------------
function cleanupPortFile() {
  const portInfoPath = getPortInfoPath();
  if (fs.existsSync(portInfoPath)) {
    try {
      fs.unlinkSync(portInfoPath);
      console.log('[main] Cleaned up old port.json');
    } catch (err) {
      console.error('[main] Failed to cleanup port.json:', err);
    }
  }
}

// ---------------------------------------------------------------------------
// 读取后端端口信息（异步，不阻塞主进程）
// ---------------------------------------------------------------------------
async function readBackendPortWithRetry(maxWait = 15000) {
  const portInfoPath = getPortInfoPath();
  const startTime = Date.now();

  while (Date.now() - startTime < maxWait) {
    if (fs.existsSync(portInfoPath)) {
      try {
        const content = fs.readFileSync(portInfoPath, 'utf-8');
        const portInfo = JSON.parse(content);
        console.log('[main] Read port info:', portInfo);
        return portInfo.port;
      } catch (err) {
        console.error('[main] Failed to read port info:', err);
      }
    }
    // 使用 setTimeout 等待，不阻塞主进程
    await new Promise(resolve => setTimeout(resolve, 200));
  }

  // 超时，使用配置文件中的端口
  console.warn('[main] Timeout reading port info, using config port');
  const config = loadConfig();
  return config?.server?.port || 8765;
}

function getBackendURL() {
  return `http://localhost:${backendPort}`;
}

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------
function setupIPC() {
  ipcMain.handle('get-backend-url', () => {
    return getBackendURL();
  });

  ipcMain.handle('get-backend-port', () => {
    return backendPort;
  });

  ipcMain.handle('get-startup-status', () => {
    return startupStatus;
  });
}

// ---------------------------------------------------------------------------
// 后端进程管理
// ---------------------------------------------------------------------------
function startPythonBackend() {
  const executable = getPythonExecutable();
  const sourceDir = getBackendSourceDir();
  const dataDir = getBackendDataDir();

  console.log(`[main] Starting backend: ${executable}`);
  console.log(`[main] Source dir: ${sourceDir}`);
  console.log(`[main] Data dir: ${dataDir}`);

  const options = {
    cwd: sourceDir,
    stdio: ['pipe', 'pipe', 'pipe'],
    shell: false,
    detached: false,
    env: {
      ...process.env,
      DENTAL_AGENT_DATA_DIR: dataDir,
      DENTAL_AGENT_DEV: isDev ? '1' : '0',
    },
  };

  pythonProcess = spawn(executable, [], options);

  pythonProcess.stdout.on('data', (data) => {
    const msg = data.toString().trim();
    console.log(`[backend:stdout] ${msg}`);
    if (msg.includes('Starting server')) {
      startupStatus = 'starting_server';
      notifyStartupStatus();
    }
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[backend:stderr] ${data.toString().trim()}`);
  });

  pythonProcess.on('error', (err) => {
    console.error('[main] Failed to start backend process:', err);
    startupStatus = 'error';
    notifyStartupStatus();
  });

  pythonProcess.on('exit', (code, signal) => {
    console.log(`[main] Backend exited with code ${code}, signal ${signal}`);
    pythonProcess = null;
    backendReady = false;
  });
}

function killPythonBackend() {
  if (!pythonProcess) return;
  console.log('[main] Killing backend process...');

  try {
    const pid = pythonProcess.pid;
    console.log(`[main] Backend PID: ${pid}`);

    if (process.platform === 'win32') {
      try {
        const { execSync } = require('child_process');
        execSync(`taskkill /pid ${pid} /T /F`, { stdio: 'ignore' });
        console.log('[main] Backend process tree killed via taskkill');
      } catch (e) {
        pythonProcess.kill('SIGTERM');
      }
    } else {
      pythonProcess.kill('SIGTERM');
      setTimeout(() => {
        if (pythonProcess) {
          console.log('[main] Force-killing backend process...');
          pythonProcess.kill('SIGKILL');
        }
      }, 3000);
    }
  } catch (err) {
    console.error('[main] Error killing backend:', err);
  }
}

// ---------------------------------------------------------------------------
// 通知前端启动状态
// ---------------------------------------------------------------------------
function notifyStartupStatus() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('startup-status', {
      status: startupStatus,
      port: backendPort,
    });
  }
}

// ---------------------------------------------------------------------------
// Health polling (非阻塞)
// ---------------------------------------------------------------------------
function pollHealthInBackground() {
  const startTime = Date.now();
  startupStatus = 'waiting_backend';
  notifyStartupStatus();

  // 异步读取后端端口
  readBackendPortWithRetry().then(port => {
    if (port !== backendPort) {
      console.log(`[main] Backend using port ${port} instead of ${backendPort}`);
      backendPort = port;
    }

    const healthEndpoint = `http://localhost:${backendPort}/api/health`;

    function check() {
      if (backendReady) return;

      const req = http.get(healthEndpoint, (res) => {
        if (res.statusCode === 200) {
          console.log('[main] Backend health check passed.');
          backendReady = true;
          startupStatus = 'ready';
          notifyStartupStatus();
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('backend-ready', {
              port: backendPort,
              url: getBackendURL(),
            });
          }
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
      if (Date.now() - startTime > 30000) {
        console.error('[main] Backend health check timed out');
        startupStatus = 'timeout';
        notifyStartupStatus();
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('backend-error', 'Backend startup timeout');
        }
        return;
      }
      setTimeout(check, 300);
    }

    check();
  });
}

// ---------------------------------------------------------------------------
// Loading HTML with progress (使用 preload 桥接)
// ---------------------------------------------------------------------------
function getLoadingHTML() {
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Dental Agent</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      height: 100vh;
      display: flex;
      justify-content: center;
      align-items: center;
    }
    .container {
      text-align: center;
      color: white;
      width: 320px;
    }
    .logo {
      font-size: 64px;
      margin-bottom: 24px;
    }
    h1 {
      font-size: 24px;
      font-weight: 500;
      margin-bottom: 32px;
    }
    .progress-bar {
      width: 100%;
      height: 4px;
      background: rgba(255,255,255,0.3);
      border-radius: 2px;
      overflow: hidden;
      margin-bottom: 16px;
    }
    .progress-fill {
      height: 100%;
      background: white;
      border-radius: 2px;
      animation: progress 2s ease-in-out infinite;
    }
    @keyframes progress {
      0% { width: 0%; }
      50% { width: 70%; }
      100% { width: 100%; }
    }
    .status {
      font-size: 14px;
      opacity: 0.9;
    }
    .spinner {
      width: 32px;
      height: 32px;
      margin: 0 auto 16px;
      border: 3px solid rgba(255,255,255,0.3);
      border-radius: 50%;
      border-top-color: white;
      animation: spin 1s ease-in-out infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">🦷</div>
    <h1>Dental Agent</h1>
    <div class="spinner"></div>
    <div class="progress-bar">
      <div class="progress-fill"></div>
    </div>
    <div class="status" id="status">Starting services...</div>
  </div>
  <script>
    // 使用 preload 暴露的 API
    if (window.electronAPI) {
      window.electronAPI.onStartupStatus((data) => {
        const statusEl = document.getElementById('status');
        const messages = {
          'initializing': 'Initializing...',
          'starting_server': 'Starting backend server...',
          'waiting_backend': 'Waiting for backend to be ready...',
          'ready': 'Ready!',
          'timeout': 'Startup timeout',
          'error': 'Startup error'
        };
        statusEl.textContent = messages[data.status] || data.status;
      });
    }
  </script>
</body>
</html>`;
}

// ---------------------------------------------------------------------------
// BrowserWindow
// ---------------------------------------------------------------------------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    title: 'Dental Agent',
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  // 先显示加载页面
  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(getLoadingHTML())}`);

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// 加载实际应用
function loadApp() {
  if (!mainWindow) return;

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    const indexPath = path.join(__dirname, '..', 'frontend', 'dist', 'index.html');
    console.log('[main] Loading app from:', indexPath);
    mainWindow.loadFile(indexPath);
  }
}

// ---------------------------------------------------------------------------
// 单实例保护
// ---------------------------------------------------------------------------
const gotTheLock = app.requestSingleInstanceLock();

if (!gotTheLock) {
  console.log('[main] Another instance is already running, quitting...');
  app.quit();
} else {
  app.on('second-instance', (event, commandLine, workingDirectory) => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  // ---------------------------------------------------------------------------
  // App lifecycle
  // ---------------------------------------------------------------------------
  app.whenReady().then(async () => {
    setDefaultCSP();
    setupIPC();

    // 清理旧的端口文件
    cleanupPortFile();

    // 读取配置获取端口
    const config = loadConfig();
    backendPort = config?.server?.port || 8765;
    console.log(`[main] Config port: ${backendPort}`);

    startupStatus = 'initializing';
    createWindow();
    startPythonBackend();
    pollHealthInBackground();

    // 监听后端就绪事件，加载应用
    const checkReady = setInterval(() => {
      if (backendReady) {
        clearInterval(checkReady);
        loadApp();
      }
    }, 500);

    // 超时后也加载应用（让用户看到错误）
    setTimeout(() => {
      if (!backendReady) {
        clearInterval(checkReady);
        loadApp();
      }
    }, 35000);

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
    cleanupPortFile();
  });

  app.on('quit', () => {
    killPythonBackend();
  });
}
