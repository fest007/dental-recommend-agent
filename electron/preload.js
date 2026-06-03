const { contextBridge, ipcRenderer } = require('electron');

// 暴露后端状态给前端
contextBridge.exposeInMainWorld('electronAPI', {
  // 获取后端 URL
  getBackendURL: () => ipcRenderer.invoke('get-backend-url'),
  // 获取后端端口
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),
  // 监听后端就绪事件
  onBackendReady: (callback) => {
    ipcRenderer.on('backend-ready', (event, data) => callback(data));
  },
  // 监听后端错误事件
  onBackendError: (callback) => {
    ipcRenderer.on('backend-error', (event, message) => callback(message));
  },
  // 移除监听器
  removeAllListeners: (channel) => {
    ipcRenderer.removeAllListeners(channel);
  },
});
