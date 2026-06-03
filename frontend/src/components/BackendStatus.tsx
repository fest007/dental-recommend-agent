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
  'crashed': { text: 'Backend crashed', percent: 100 },
  'timeout': { text: 'Startup timeout', percent: 100 },
  'error': { text: 'Startup error', percent: 100 },
  'restarting': { text: 'Restarting backend...', percent: 20 },
}

// 获取后端 URL
async function getBackendURL(): Promise<string> {
  try {
    if (window.electronAPI) {
      return await window.electronAPI.getBackendURL()
    }
  } catch {
    // ignore
  }
  if (window.location.protocol === 'file:') {
    return 'http://localhost:8765'
  }
  return ''
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
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)
  const [elapsedTime, setElapsedTime] = useState(0)
  const [isRetrying, setIsRetrying] = useState(false)

  // 计时器
  useEffect(() => {
    if (isReady) return
    const timer = setInterval(() => {
      setElapsedTime(prev => prev + 1)
    }, 1000)
    return () => clearInterval(timer)
  }, [isReady])

  const checkStatus = useCallback(async () => {
    setErrorMessage(null)

    const maxRetries = 60
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

    setErrorMessage('Backend service failed to start.')
  }, [])

  // 重试：真正重启后端
  const handleRetry = useCallback(async () => {
    setIsRetrying(true)
    setElapsedTime(0)
    setErrorMessage(null)
    setStartupStatus({ status: 'restarting' })

    if (window.electronAPI) {
      try {
        // 通知主进程重启后端
        await window.electronAPI.restartBackend()
        // 主进程会通过事件通知状态变化
      } catch (err) {
        console.error('Failed to restart backend:', err)
        setErrorMessage('Failed to restart backend service.')
      }
    } else {
      // 浏览器环境，只重置状态
      setRetryCount(c => c + 1)
    }

    setIsRetrying(false)
  }, [])

  useEffect(() => {
    // 监听 Electron 启动状态
    if (window.electronAPI) {
      window.electronAPI.onStartupStatus((data: StartupStatus) => {
        setStartupStatus(data)
        // 清除之前的错误（如果状态变化了）
        if (data.status !== 'crashed' && data.status !== 'timeout' && data.status !== 'error') {
          setErrorMessage(null)
        }
      })

      window.electronAPI.onBackendReady((data: { port: number; url: string }) => {
        console.log('[BackendStatus] Backend ready:', data)
        setIsReady(true)
        setErrorMessage(null)
      })

      window.electronAPI.onBackendError((message: string) => {
        setErrorMessage(message)
      })

      // 获取初始状态，如果后端已经 ready 则直接标记
      window.electronAPI.getStartupStatus().then((status: string) => {
        setStartupStatus({ status })
        if (status === 'ready') {
          setIsReady(true)
        }
      })

      return () => {
        window.electronAPI?.removeAllListeners('startup-status')
        window.electronAPI?.removeAllListeners('backend-ready')
        window.electronAPI?.removeAllListeners('backend-error')
      }
    } else {
      // 浏览器环境，使用轮询
      checkStatus()
    }
  }, [checkStatus, retryCount])

  // React 主应用挂载后通知主进程，写 renderer-ready marker
  useEffect(() => {
    if (isReady && window.electronAPI?.notifyRendererReady) {
      window.electronAPI.notifyRendererReady()
    }
  }, [isReady])

  if (isReady) {
    return <>{children}</>
  }

  const statusInfo = STATUS_MESSAGES[startupStatus.status] || { text: startupStatus.status, percent: 50 }
  const isError = startupStatus.status === 'crashed' ||
                  startupStatus.status === 'timeout' ||
                  startupStatus.status === 'error' ||
                  errorMessage

  // 根据错误类型显示不同的提示
  const getErrorHint = () => {
    if (errorMessage) return errorMessage
    switch (startupStatus.status) {
      case 'crashed':
        return 'The backend service crashed unexpectedly. This could be due to missing dependencies or configuration errors.'
      case 'timeout':
        return 'The backend service took too long to start. This could be due to a port conflict or resource issue.'
      case 'error':
        return 'Failed to start the backend service. Please check the logs for more details.'
      default:
        return 'An unknown error occurred.'
    }
  }

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
              fontSize: 16,
              marginBottom: 8,
              fontWeight: 500,
            }}
          >
            {statusInfo.text}
          </Text>
          <Text
            style={{
              color: 'rgba(255,255,255,0.8)',
              fontSize: 13,
              marginBottom: 24,
              textAlign: 'center',
              maxWidth: 360,
              lineHeight: 1.5,
            }}
          >
            {getErrorHint()}
          </Text>
          <Button
            icon={<ReloadOutlined />}
            onClick={handleRetry}
            loading={isRetrying}
            size="large"
          >
            Retry
          </Button>
        </>
      )}
    </div>
  )
}
