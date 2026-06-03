import React, { useState, useEffect, useCallback } from 'react'
import { Spin, Typography, Button, Progress } from 'antd'
import { LoadingOutlined, ReloadOutlined } from '@ant-design/icons'

const { Text } = Typography

interface StartupStatus {
  status: string
  port?: number
}

const STATUS_MESSAGES: Record<string, { text: string; percent: number }> = {
  'initializing': { text: 'Initializing application...', percent: 10 },
  'starting_server': { text: 'Starting backend server...', percent: 30 },
  'waiting_backend': { text: 'Waiting for backend to be ready...', percent: 60 },
  'ready': { text: 'Ready!', percent: 100 },
  'timeout': { text: 'Startup timeout', percent: 100 },
  'error': { text: 'Startup error', percent: 100 },
}

// 获取后端 URL
async function getBackendURL(): Promise<string> {
  // Electron 环境中，从主进程获取
  try {
    const { ipcRenderer } = window.require('electron')
    return await ipcRenderer.invoke('get-backend-url')
  } catch {
    // 浏览器环境
    if (window.location.protocol === 'file:') {
      return 'http://localhost:8765'
    }
    return ''
  }
}

// 后端健康检查
const checkBackendHealth = async (): Promise<boolean> => {
  try {
    const baseURL = await getBackendURL()
    const res = await fetch(`${baseURL}/api/health`, {
      method: 'GET',
      signal: AbortSignal.timeout(2000),
    })
    return res.ok
  } catch {
    return false
  }
}

interface BackendStatusProps {
  children: React.ReactNode
}

export default function BackendStatus({ children }: BackendStatusProps) {
  const [isReady, setIsReady] = useState(false)
  const [startupStatus, setStartupStatus] = useState<StartupStatus>({ status: 'initializing' })
  const [error, setError] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)
  const [elapsedTime, setElapsedTime] = useState(0)

  // 计时器
  useEffect(() => {
    if (isReady) return
    const timer = setInterval(() => {
      setElapsedTime(prev => prev + 1)
    }, 1000)
    return () => clearInterval(timer)
  }, [isReady])

  const checkStatus = useCallback(async () => {
    setError(null)

    const maxRetries = 60 // 最多检查 60 次（约 30 秒）
    let count = 0

    while (count < maxRetries) {
      const healthy = await checkBackendHealth()
      if (healthy) {
        setIsReady(true)
        setStartupStatus({ status: 'ready' })
        return
      }
      count++
      await new Promise((resolve) => setTimeout(resolve, 500))
    }

    setError('Backend startup timeout. Please check if port is in use.')
  }, [])

  useEffect(() => {
    // 监听 Electron 启动状态
    try {
      const { ipcRenderer } = window.require('electron')

      ipcRenderer.on('startup-status', (event: any, data: StartupStatus) => {
        setStartupStatus(data)
      })

      ipcRenderer.on('backend-ready', (event: any, data: { port: number; url: string }) => {
        console.log('[BackendStatus] Backend ready:', data)
        setIsReady(true)
      })

      ipcRenderer.on('backend-error', (event: any, message: string) => {
        setError(message)
      })

      // 获取初始状态
      ipcRenderer.invoke('get-startup-status').then((status: string) => {
        setStartupStatus({ status })
      })

      return () => {
        ipcRenderer.removeAllListeners('startup-status')
        ipcRenderer.removeAllListeners('backend-ready')
        ipcRenderer.removeAllListeners('backend-error')
      }
    } catch {
      // 浏览器环境，使用轮询
      checkStatus()
    }
  }, [checkStatus, retryCount])

  if (isReady) {
    return <>{children}</>
  }

  const statusInfo = STATUS_MESSAGES[startupStatus.status] || { text: startupStatus.status, percent: 50 }
  const isError = startupStatus.status === 'timeout' || startupStatus.status === 'error' || error

  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        color: '#fff',
      }}
    >
      <div style={{ fontSize: 64, marginBottom: 24 }}>🦷</div>
      <Text
        style={{
          fontSize: 28,
          fontWeight: 500,
          color: '#fff',
          marginBottom: 32,
        }}
      >
        Dental Agent
      </Text>

      {!isError ? (
        <>
          <Spin
            indicator={<LoadingOutlined style={{ fontSize: 24, color: '#fff' }} />}
          />
          <div style={{ width: 240, marginTop: 24 }}>
            <Progress
              percent={statusInfo.percent}
              showInfo={false}
              strokeColor="#fff"
              trailColor="rgba(255,255,255,0.3)"
            />
          </div>
          <Text
            style={{
              marginTop: 16,
              color: 'rgba(255,255,255,0.9)',
              fontSize: 14,
            }}
          >
            {statusInfo.text}
          </Text>
          <Text
            style={{
              marginTop: 8,
              color: 'rgba(255,255,255,0.6)',
              fontSize: 12,
            }}
          >
            {elapsedTime}s elapsed
          </Text>
        </>
      ) : (
        <>
          <Text
            style={{
              color: '#ff6b6b',
              fontSize: 14,
              marginBottom: 8,
            }}
          >
            {error || statusInfo.text}
          </Text>
          <Text
            style={{
              color: 'rgba(255,255,255,0.7)',
              fontSize: 12,
              marginBottom: 16,
            }}
          >
            Please check if another instance is running or port 8765 is in use.
          </Text>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              setRetryCount((c) => c + 1)
              setElapsedTime(0)
              setStartupStatus({ status: 'initializing' })
              setError(null)
            }}
          >
            Retry
          </Button>
        </>
      )}
    </div>
  )
}
