const { app, BrowserWindow, session, dialog, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const http = require('http');
const yaml = require('js-yaml');

let mainWindow = null;
let pythonProcess = null;
let backendReady = false;
let backendPort = 8765; // 默认端口，会从配置文件更新

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

  // 如果配置文件不存在，创建默认配置
  if (!fs.existsSync(configPath)) {
    if (fs.existsSync(examplePath)) {
      console.log('[main] Creating config.yaml from example...');
      fs.copyFileSync(examplePath, configPath);
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
      fs.writeFileSync(configPath, defaultConfig);
    }
  }

  // 读取配置
  try {
    const configContent = fs.readFileSync(configPath, 'utf8');
    const config = yaml.load(configContent);
    return config || {};
  } catch (err) {
    console.error('[main] Failed to load config:', err);
    return {};
  }
}

function getBackendPort() {
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
  // 前端请求获取后端 URL
  ipcMain.handle('get-backend-url', () => {
    return getBackendURL();
  });

  // 前端请求获取后端端口
  ipcMain.handle('get-backend-port', () => {
    return backendPort;
  });
}

// ---------------------------------------------------------------------------
// 后端进程管理
// ---------------------------------------------------------------------------
function startPythonBackend() {
  const executable = getPythonExecutable();
  const sourceDir = getBackendSourceDir();
  const dataDir = getBackendDataDir();

  // 从配置文件读取端口
  backendPort = getBackendPort();

  console.log(`[main] Starting backend: ${executable}`);
  console.log(`[main] Source dir: ${sourceDir}`);
  console.log(`[main] Data dir: ${dataDir}`);
  console.log(`[main] Backend port: ${backendPort}`);

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
// Health polling (非阻塞)
// ---------------------------------------------------------------------------
function pollHealthInBackground() {
  const startTime = Date.now();
  const healthEndpoint = `http://localhost:${backendPort}/api/health`;

  function check() {
    if (backendReady) return;

    const req = http.get(healthEndpoint, (res) => {
      if (res.statusCode === 200) {
        console.log('[main] Backend health check passed.');
        backendReady = true;
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
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('backend-error', '后端服务启动超时');
      }
      return;
    }
    setTimeout(check, 300);
  }

  check();
}

// ---------------------------------------------------------------------------
// BrowserWindow
// ---------------------------------------------------------------------------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    title: '牙科设备推荐Agent',
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    const indexPath = path.join(__dirname, '..', 'frontend', 'dist', 'index.html');
    console.log('[main] Loading app from:', indexPath);
    mainWindow.loadFile(indexPath);
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error('[main] Failed to load:', errorCode, errorDescription);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
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

    // 读取配置获取端口
    backendPort = getBackendPort();
    console.log(`[main] Using backend port: ${backendPort}`);

    createWindow();
    startPythonBackend();
    pollHealthInBackground();

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
}
