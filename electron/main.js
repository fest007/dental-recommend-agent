const { app, BrowserWindow, session, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const http = require('http');

let mainWindow = null;
let pythonProcess = null;
let backendReady = false;

const isDev = !app.isPackaged;
const BACKEND_PORT = 8765;
const BACKEND_URL = `http://localhost:${BACKEND_PORT}`;
const HEALTH_ENDPOINT = `${BACKEND_URL}/api/health`;
const HEALTH_POLL_INTERVAL = 300;
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
  // 后端可执行文件所在目录（只读）
  if (isDev) {
    return path.join(__dirname, '..', 'backend');
  }
  return path.join(process.resourcesPath, 'backend');
}

function getBackendDataDir() {
  // 后端数据目录（可写）- 使用 userData
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

// 初始化配置文件（如果不存在）
function initConfig() {
  const dataDir = getBackendDataDir();
  const configPath = path.join(dataDir, 'config.yaml');
  const sourceDir = getBackendSourceDir();
  const examplePath = path.join(sourceDir, 'config.yaml.example');

  if (!fs.existsSync(configPath)) {
    if (fs.existsSync(examplePath)) {
      console.log('[main] Creating config.yaml from example...');
      fs.copyFileSync(examplePath, configPath);
    } else {
      // 创建默认配置
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
  port: ${BACKEND_PORT}

database:
  path: "app.db"

qdrant:
  path: "qdrant"
  collection: "products"
`;
      fs.writeFileSync(configPath, defaultConfig);
    }
  }
}

function startPythonBackend() {
  const executable = getPythonExecutable();
  const sourceDir = getBackendSourceDir();
  const dataDir = getBackendDataDir();

  console.log(`[main] Starting backend: ${executable}`);
  console.log(`[main] Source dir: ${sourceDir}`);
  console.log(`[main] Data dir: ${dataDir}`);

  // 直接执行，不使用 shell（避免孤儿进程）
  const options = {
    cwd: sourceDir,
    stdio: ['pipe', 'pipe', 'pipe'],
    shell: false,  // 不使用 shell，直接执行
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
    // 获取进程 PID
    const pid = pythonProcess.pid;
    console.log(`[main] Backend PID: ${pid}`);

    if (process.platform === 'win32') {
      // Windows: 使用 taskkill 杀死整个进程树
      try {
        const { execSync } = require('child_process');
        execSync(`taskkill /pid ${pid} /T /F`, { stdio: 'ignore' });
        console.log('[main] Backend process tree killed via taskkill');
      } catch (e) {
        // taskkill 可能失败（进程已退出），尝试直接 kill
        pythonProcess.kill('SIGTERM');
      }
    } else {
      // macOS/Linux: 先 SIGTERM，超时后 SIGKILL
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
// Health polling (非阻塞)
// ---------------------------------------------------------------------------
function pollHealthInBackground() {
  const startTime = Date.now();

  function check() {
    if (backendReady) return;

    const req = http.get(HEALTH_ENDPOINT, (res) => {
      if (res.statusCode === 200) {
        console.log('[main] Backend health check passed.');
        backendReady = true;
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('backend-ready');
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
    if (Date.now() - startTime > HEALTH_POLL_TIMEOUT) {
      console.error('[main] Backend health check timed out');
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('backend-error', '后端服务启动超时');
      }
      return;
    }
    setTimeout(check, HEALTH_POLL_INTERVAL);
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

  // 立即加载前端
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
// App lifecycle
// ---------------------------------------------------------------------------
app.whenReady().then(async () => {
  setDefaultCSP();

  // 初始化配置文件
  initConfig();

  // 立即创建窗口并加载前端
  createWindow();

  // 后台启动后端
  startPythonBackend();

  // 后台轮询后端健康状态
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
