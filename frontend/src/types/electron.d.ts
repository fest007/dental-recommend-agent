interface ElectronAPI {
  getBackendURL: () => Promise<string>
  getBackendPort: () => Promise<number>
  getStartupStatus: () => Promise<string | { status: string; error?: string }>
  restartBackend: () => Promise<{ status: string }>
  notifyRendererReady: () => Promise<void>
  onBackendReady: (callback: (data: { port: number; url: string }) => void) => void
  onBackendError: (callback: (message: string) => void) => void
  onStartupStatus: (callback: (data: { status: string; port?: number; error?: string }) => void) => void
  removeAllListeners: (channel: string) => void
}

interface Window {
  electronAPI?: ElectronAPI
}
