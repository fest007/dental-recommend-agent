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
let healthCheckTimer = null;
let backendLogFile = null;
let backendExited = false;
let backendExitCode = null;

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

function getBackendURL() {
  return `http://127.0.0.1:${backendPort}`;
}

function getPortFilePath() {
  return path.join(getBackendDataDir(), 'port.json');
}

function readPortFromFile() {
  try {
    const portFile = getPortFilePath();
    if (fs.existsSync(portFile)) {
      const info = JSON.parse(fs.readFileSync(portFile, 'utf-8'));
      if (info && typeof info.port === 'number') {
        return info.port;
      }
    }
  } catch (err) {
    console.error('[main] Failed to read port.json:', err.message);
  }
  return null;
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

  ipcMain.handle('notify-renderer-ready', () => {
    try {
      fs.writeFileSync(path.join(getBackendDataDir(), 'renderer-ready'), String(Date.now()));
    } catch {}
  });

  ipcMain.handle('restart-backend', async () => {
    console.log('[main] Restarting backend...');
    // 清掉上一轮的健康检查定时器，防止旧轮询串进新一轮
    if (healthCheckTimer) {
      clearTimeout(healthCheckTimer);
      healthCheckTimer = null;
    }
    killPythonBackend();
    await new Promise(resolve => setTimeout(resolve, 500));
    backendExited = false;
    backendExitCode = null;
    backendReady = false;
    startBackendWithHealthCheck();
    return { status: 'restarting' };
  });
}

// ---------------------------------------------------------------------------
// 后端进程管理
// ---------------------------------------------------------------------------
function startPythonBackend() {
  const executable = getPythonExecutable();
  const sourceDir = getBackendSourceDir();
  const dataDir = getBackendDataDir();

  // 创建日志文件
  const logDir = path.join(dataDir, 'logs');
  if (!fs.existsSync(logDir)) {
    fs.mkdirSync(logDir, { recursive: true });
  }
  const logPath = path.join(logDir, 'backend.log');
  backendLogFile = fs.openSync(logPath, 'a');
  console.log(`[main] Backend log file: ${logPath}`);

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
  backendExited = false;
  backendExitCode = null;

  pythonProcess.stdout.on('data', (data) => {
    const msg = data.toString().trim();
    console.log(`[backend:stdout] ${msg}`);
    if (backendLogFile) {
      fs.writeSync(backendLogFile, `[stdout] ${msg}\n`);
    }
    if (msg.includes('Starting server') || msg.includes('Uvicorn running')) {
      startupStatus = 'starting_server';
      notifyStartupStatus();
    }
  });

  pythonProcess.stderr.on('data', (data) => {
    const msg = data.toString().trim();
    console.error(`[backend:stderr] ${msg}`);
    if (backendLogFile) {
      fs.writeSync(backendLogFile, `[stderr] ${msg}\n`);
    }
  });

  pythonProcess.on('error', (err) => {
    console.error('[main] Failed to start backend process:', err);
    if (backendLogFile) {
      fs.writeSync(backendLogFile, `[spawn error] ${err.message}\n`);
    }
    startupStatus = 'error';
    notifyStartupStatus();
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend-error', `Failed to start: ${err.message}`);
    }
  });

  pythonProcess.on('exit', (code, signal) => {
    console.log(`[main] Backend exited with code ${code}, signal ${signal}`);
    backendExited = true;
    backendExitCode = code;
    if (backendLogFile) {
      fs.writeSync(backendLogFile, `[exit] code=${code} signal=${signal}\n`);
      fs.closeSync(backendLogFile);
      backendLogFile = null;
    }
    pythonProcess = null;
    backendReady = false;

    // 如果不是正常退出且还没 ready，通知前端
    if (code !== 0 && code !== null && startupStatus !== 'ready') {
      startupStatus = 'crashed';
      notifyStartupStatus();

      let errorMsg = `Backend crashed with exit code ${code}`;
      try {
        const logPath = path.join(logDir, 'backend.log');
        const logContent = fs.readFileSync(logPath, 'utf-8');
        const lines = logContent.split('\n').filter(l => l.trim());
        const lastLines = lines.slice(-10).join('\n');
        if (lastLines) {
          errorMsg = `Backend crashed (exit code ${code}):\n${lastLines}`;
        }
      } catch {}

      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('backend-error', errorMsg);
      }
    }
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
// 健康检查
// ---------------------------------------------------------------------------
function startHealthCheck() {
  const startTime = Date.now();
  const maxWait = 60000; // 60 秒超时

  function currentEndpoint() {
    return `http://127.0.0.1:${backendPort}/api/health`;
  }

  console.log(`[main] Starting health check, waiting for port.json from backend`);

  function check() {
    // 如果后端已经退出，停止检查
    if (backendExited) {
      console.log('[main] Backend exited, stopping health check');
      return;
    }

    if (backendReady) return;

    // 从 port.json 读取后端实际选用的端口
    const filePort = readPortFromFile();
    if (filePort !== null && filePort !== backendPort) {
      console.log(`[main] Port updated from port.json: ${backendPort} -> ${filePort}`);
      backendPort = filePort;
    }

    const healthEndpoint = currentEndpoint();
    const req = http.get(healthEndpoint, (res) => {
      if (res.statusCode === 200) {
        console.log('[main] Backend health check passed!');
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
        scheduleRetry();
      }
      res.resume();
    });

    req.on('error', (err) => {
      console.log(`[main] Health check error: ${err.message}`);
      scheduleRetry();
    });

    req.setTimeout(3000, () => {
      req.destroy();
      scheduleRetry();
    });
  }

  function scheduleRetry() {
    if (backendExited) {
      console.log('[main] Backend exited, stopping health check retries');
      return;
    }

    if (Date.now() - startTime > maxWait) {
      console.error('[main] Health check timed out');
      startupStatus = 'timeout';
      notifyStartupStatus();
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('backend-error', 'Backend startup timeout');
      }
      return;
    }

    healthCheckTimer = setTimeout(check, 500);
  }

  // 开始第一次检查
  check();
}

// ---------------------------------------------------------------------------
// 启动后端 + 健康检查
// ---------------------------------------------------------------------------
function startBackendWithHealthCheck() {
  // 清掉残留的健康检查定时器
  if (healthCheckTimer) {
    clearTimeout(healthCheckTimer);
    healthCheckTimer = null;
  }

  const config = loadConfig();
  backendPort = config?.server?.port || 8765;
  console.log(`[main] Config port: ${backendPort}`);

  startupStatus = 'initializing';
  notifyStartupStatus();

  // 在启动后端之前清理旧的 port.json 和 renderer-ready marker
  // 放在 spawn 之前，这样后端写出的新文件不会被误删
  try {
    const portFile = getPortFilePath();
    if (fs.existsSync(portFile)) fs.unlinkSync(portFile);
    const markerFile = path.join(getBackendDataDir(), 'renderer-ready');
    if (fs.existsSync(markerFile)) fs.unlinkSync(markerFile);
  } catch {}

  // 启动后端
  startPythonBackend();

  // 开始健康检查
  startupStatus = 'waiting_backend';
  notifyStartupStatus();

  startHealthCheck();
}

// ---------------------------------------------------------------------------
// Loading HTML
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
    .logo { font-size: 64px; margin-bottom: 24px; }
    h1 { font-size: 24px; font-weight: 500; margin-bottom: 32px; }
    .spinner {
      width: 32px; height: 32px;
      margin: 0 auto 16px;
      border: 3px solid rgba(255,255,255,0.3);
      border-radius: 50%;
      border-top-color: white;
      animation: spin 1s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .status { font-size: 14px; opacity: 0.9; }
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">🦷</div>
    <h1>Dental Agent</h1>
    <div class="spinner"></div>
    <div class="status" id="status">Starting...</div>
  </div>
  <script>
    if (window.electronAPI) {
      window.electronAPI.onStartupStatus((data) => {
        const el = document.getElementById('status');
        const msgs = {
          'initializing': 'Initializing...',
          'starting_server': 'Starting server...',
          'waiting_backend': 'Waiting for backend...',
          'ready': 'Ready!',
          'crashed': 'Backend crashed',
          'timeout': 'Startup timeout',
          'error': 'Startup error',
          'restarting': 'Restarting...'
        };
        el.textContent = msgs[data.status] || data.status;
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

  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(getLoadingHTML())}`);

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

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

  app.whenReady().then(async () => {
    setDefaultCSP();
    setupIPC();

    createWindow();
    startBackendWithHealthCheck();

    // 等待后端就绪或超时后加载应用
    const checkReady = setInterval(() => {
      if (backendReady || backendExited || startupStatus === 'timeout' || startupStatus === 'crashed') {
        clearInterval(checkReady);
        loadApp();
      }
    }, 500);

    // 最多等待 65 秒
    setTimeout(() => {
      clearInterval(checkReady);
      loadApp();
    }, 65000);

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
    if (healthCheckTimer) {
      clearTimeout(healthCheckTimer);
    }
    killPythonBackend();
  });

  app.on('quit', () => {
    killPythonBackend();
  });
}
