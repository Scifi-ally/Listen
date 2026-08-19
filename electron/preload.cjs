const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('listenDesktop', {
  minimize: () => ipcRenderer.send('window:minimize'),
  maximize: () => ipcRenderer.send('window:maximize'),
  close: () => ipcRenderer.send('window:close'),
  onBackendError: (callback) => ipcRenderer.on('backend-error', (_event, message) => callback(message)),
});
