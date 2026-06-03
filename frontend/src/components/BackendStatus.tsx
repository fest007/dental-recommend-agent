import React, { useState, useEffect, useCallback } from 'react'
import { Spin, Typography, Button } from 'antd'
import { LoadingOutlined, ReloadOutlined } from '@ant-design/icons'

const { Text } = Typography

// 获取后端 URL
async function getBackendURL(): Promise<string> {
  // Electron 环境中，从主进程获取
  const electronAPI = (window as any).electronAPI
  if (electronAPI) {
    try {
      return await electronAPI.getBackendURL()
    } catch (err) {
      console.error('Failed to get backend URL:', err)
    }
  }
  // 浏览器环境
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
  const [isChecking, setIsChecking] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)

  const checkStatus = useCallback(async () => {
    setIsChecking(true)
    setError(null)

    const maxRetries = 30
    let count = 0

    while (count < maxRetries) {
      const healthy = await checkBackendHealth()
      if (healthy) {
        setIsReady(true)
        setIsChecking(false)
        return
      }
      count++
      await new Promise((resolve) => setTimeout(resolve, 500))
    }

    setIsChecking(false)
    setError('后端服务启动超时，请检查端口是否被占用')
  }, [])

  useEffect(() => {
    checkStatus()

    // 监听 Electron 通知
    const electronAPI = (window as any).electronAPI
    if (electronAPI) {
      electronAPI.onBackendReady((data: { port: number; url: string }) => {
        console.log('[BackendStatus] Backend ready:', data)
        setIsReady(true)
        setIsChecking(false)
      })
      electronAPI.onBackendError((message: string) => {
        setError(message)
        setIsChecking(false)
      })
    }

    return () => {
      if (electronAPI) {
        electronAPI.removeAllListeners('backend-ready')
        electronAPI.removeAllListeners('backend-error')
      }
    }
  }, [checkStatus, retryCount])

  if (isReady) {
    return <>{children}</>
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
        牙科设备推荐Agent
      </Text>

      {isChecking ? (
        <>
          <Spin
            indicator={<LoadingOutlined style={{ fontSize: 32, color: '#fff' }} />}
          />
          <Text
            style={{
              marginTop: 16,
              color: 'rgba(255,255,255,0.8)',
              fontSize: 14,
            }}
          >
            正在启动服务...
          </Text>
        </>
      ) : error ? (
        <>
          <Text
            style={{
              color: '#ff6b6b',
              fontSize: 14,
              marginBottom: 16,
            }}
          >
            {error}
          </Text>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              setRetryCount((c) => c + 1)
            }}
          >
            重试
          </Button>
        </>
      ) : null}
    </div>
  )
}
