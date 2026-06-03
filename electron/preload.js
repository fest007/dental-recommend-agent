const { contextBridge, ipcRenderer } = require('electron');

// 暴露后端状态给前端
contextBridge.exposeInMainWorld('electronAPI', {
  // 监听后端就绪事件
  onBackendReady: (callback) => {
    ipcRenderer.on('backend-ready', () => callback());
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
