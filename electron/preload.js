const { contextBridge, ipcRenderer } = require('electron');

// 暴露安全的 API 给前端
contextBridge.exposeInMainWorld('electronAPI', {
  // 后端相关
  getBackendURL: () => ipcRenderer.invoke('get-backend-url'),
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),
  getStartupStatus: () => ipcRenderer.invoke('get-startup-status'),

  // 重启后端
  restartBackend: () => ipcRenderer.invoke('restart-backend'),

  // 监听事件
  onBackendReady: (callback) => {
    ipcRenderer.on('backend-ready', (event, data) => callback(data));
  },
  onBackendError: (callback) => {
    ipcRenderer.on('backend-error', (event, message) => callback(message));
  },
  onStartupStatus: (callback) => {
    ipcRenderer.on('startup-status', (event, data) => callback(data));
  },

  // 清理监听器
  removeAllListeners: (channel) => {
    ipcRenderer.removeAllListeners(channel);
  },
});
