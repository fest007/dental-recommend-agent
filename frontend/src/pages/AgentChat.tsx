import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Card, Input, Button, Space, Avatar, Spin, Typography, Tag, Collapse, Tooltip } from 'antd'
import {
  SendOutlined, UserOutlined, RobotOutlined, ClearOutlined,
  CheckOutlined, CloseOutlined, ToolOutlined, LoadingOutlined,
  BulbOutlined, RightOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import {
  sendMessageStream,
  resumeChatStream,
  type ChatMessage,
  type StreamEvent,
  type StreamComponent,
} from '../services/api'
import styles from './AgentChat.module.less'

const { TextArea } = Input
const { Text } = Typography

const WELCOME_MESSAGE: ChatMessage = {
  role: 'assistant',
  content: `你好！我是牙科设备推荐专家Agent。

我可以帮你：
- 查询和了解牙科设备、耗材、配件的产品信息
- 查看用户画像和采购偏好分析
- 解释推荐结果和推荐理由
- 提供采购建议和补货提醒

请问有什么可以帮你的？`,
}

// 中间过程事件接口
interface ThinkingStep {
  id: string
  component: StreamComponent
  content: string
  tool?: string
  input?: string
  output?: string
  timestamp: number
}

// 渲染中间过程组件
function ThinkingProcess({ steps }: { steps: ThinkingStep[] }) {
  if (steps.length === 0) return null

  const getStepIcon = (component: StreamComponent) => {
    switch (component) {
      case 'thinking':
        return <BulbOutlined style={{ color: '#faad14' }} />
      case 'tool_call':
        return <ToolOutlined style={{ color: '#1890ff' }} />
      case 'tool_execution':
        return <LoadingOutlined style={{ color: '#1890ff' }} />
      case 'tool_result':
        return <CheckOutlined style={{ color: '#52c41a' }} />
      case 'tool_error':
        return <CloseOutlined style={{ color: '#ff4d4f' }} />
      case 'flow':
        return <RightOutlined style={{ color: '#722ed1' }} />
      default:
        return <BulbOutlined style={{ color: '#999' }} />
    }
  }

  const getStepLabel = (step: ThinkingStep) => {
    switch (step.component) {
      case 'thinking':
        return '思考'
      case 'tool_call':
        return `调用工具: ${step.tool || ''}`
      case 'tool_execution':
        return '执行中'
      case 'tool_result':
        return `工具结果: ${step.tool || ''}`
      case 'tool_error':
        return `工具错误: ${step.tool || ''}`
      case 'flow':
        return '流程'
      default:
        return '处理中'
    }
  }

  return (
    <div className={styles.thinkingProcess}>
      <Collapse
        ghost
        size="small"
        items={[
          {
            key: 'thinking',
            label: (
              <Space size="small">
                <BulbOutlined style={{ color: '#faad14' }} />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  思考过程 ({steps.length} 步)
                </Text>
              </Space>
            ),
            children: (
              <div className={styles.thinkingSteps}>
                {steps.map((step, idx) => (
                  <div key={step.id} className={styles.thinkingStep}>
                    <div className={styles.stepHeader}>
                      <Space size="small">
                        {getStepIcon(step.component)}
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {getStepLabel(step)}
                        </Text>
                      </Space>
                      {idx < steps.length - 1 && (
                        <div className={styles.stepConnector} />
                      )}
                    </div>
                    <div className={styles.stepContent}>
                      <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>
                        {step.content}
                      </Text>
                      {step.input && (
                        <div className={styles.stepDetail}>
                          <Text type="secondary" style={{ fontSize: 11 }}>输入:</Text>
                          <pre className={styles.codeBlock}>{step.input}</pre>
                        </div>
                      )}
                      {step.output && (
                        <div className={styles.stepDetail}>
                          <Text type="secondary" style={{ fontSize: 11 }}>输出:</Text>
                          <pre className={styles.codeBlock}>{step.output}</pre>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ),
          },
        ]}
      />
    </div>
  )
}

export default function AgentChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME_MESSAGE])
  const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([])
  const [inputValue, setInputValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [threadId, setThreadId] = useState<string | null>(null)
  const [pendingAction, setPendingAction] = useState<StreamEvent['action'] | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<(() => void) | null>(null)
  const stepIdRef = useRef(0)

  useEffect(() => {
    const behavior: ScrollBehavior = streaming ? 'auto' : 'smooth'
    messagesEndRef.current?.scrollIntoView({ behavior, block: 'end' })
  }, [messages, streaming, thinkingSteps])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.()
    }
  }, [])

  const addThinkingStep = useCallback((event: StreamEvent) => {
    const id = `step_${++stepIdRef.current}`
    const step: ThinkingStep = {
      id,
      component: event.component || 'thinking',
      content: event.content || '',
      tool: event.tool,
      input: event.input,
      output: event.output,
      timestamp: Date.now(),
    }
    setThinkingSteps(prev => [...prev, step])
    return id
  }, [])

  const handleSend = useCallback(() => {
    const text = inputValue.trim()
    if (!text || loading || streaming) return

    const userMessage: ChatMessage = { role: 'user', content: text }
    const updatedMessages = [...messages, userMessage]
    setMessages(updatedMessages)
    setInputValue('')
    setLoading(true)
    setStreaming(true)
    setPendingAction(null)
    setThinkingSteps([])

    // Add empty assistant message for streaming
    const assistantMessage: ChatMessage = { role: 'assistant', content: '' }
    setMessages([...updatedMessages, assistantMessage])

    abortRef.current = sendMessageStream(
      text,
      threadId,
      (event: StreamEvent) => {
        switch (event.type) {
          case 'thread_id':
            setThreadId(event.thread_id || null)
            break

          case 'thinking':
          case 'tool_call':
          case 'tool_result':
            // 添加思考步骤
            addThinkingStep(event)
            break

          case 'token':
            setMessages((prev) => {
              const newMessages = [...prev]
              const lastMsg = newMessages[newMessages.length - 1]
              if (lastMsg.role === 'assistant') {
                newMessages[newMessages.length - 1] = {
                  ...lastMsg,
                  content: lastMsg.content + (event.content || ''),
                }
              }
              return newMessages
            })
            break

          case 'action':
            setPendingAction(event.action || null)
            break

          case 'done':
            setLoading(false)
            setStreaming(false)
            // Update final response if needed
            if (event.response) {
              setMessages((prev) => {
                const newMessages = [...prev]
                const lastMsg = newMessages[newMessages.length - 1]
                if (lastMsg.role === 'assistant') {
                  newMessages[newMessages.length - 1] = {
                    ...lastMsg,
                    content: event.response || lastMsg.content,
                  }
                }
                return newMessages
              })
            }
            break

          case 'error':
            setMessages((prev) => {
              const newMessages = [...prev]
              const lastMsg = newMessages[newMessages.length - 1]
              if (lastMsg.role === 'assistant') {
                newMessages[newMessages.length - 1] = {
                  ...lastMsg,
                  content: `抱歉，发生错误：${event.message}`,
                }
              }
              return newMessages
            })
            setLoading(false)
            setStreaming(false)
            break
        }
      },
      (error) => {
        setMessages((prev) => {
          const newMessages = [...prev]
          const lastMsg = newMessages[newMessages.length - 1]
          if (lastMsg.role === 'assistant') {
            newMessages[newMessages.length - 1] = {
              ...lastMsg,
              content: '抱歉，发送消息失败，请稍后再试。',
            }
          }
          return newMessages
        })
        setLoading(false)
        setStreaming(false)
      },
    )
  }, [inputValue, loading, streaming, messages, threadId, addThinkingStep])

  const handleResume = useCallback((approved: boolean) => {
    if (!threadId || !pendingAction) return

    setLoading(true)
    setStreaming(true)
    setThinkingSteps([])

    // Add user confirmation message
    const confirmMsg: ChatMessage = {
      role: 'user',
      content: approved ? '✅ 确认执行' : '❌ 取消操作',
    }
    const updatedMessages = [...messages, confirmMsg]
    setMessages(updatedMessages)

    // Add empty assistant message for streaming
    const assistantMessage: ChatMessage = { role: 'assistant', content: '' }
    setMessages([...updatedMessages, assistantMessage])
    setPendingAction(null)

    abortRef.current = resumeChatStream(
      threadId,
      approved,
      (event: StreamEvent) => {
        switch (event.type) {
          case 'thinking':
          case 'tool_call':
          case 'tool_result':
            addThinkingStep(event)
            break

          case 'token':
            setMessages((prev) => {
              const newMessages = [...prev]
              const lastMsg = newMessages[newMessages.length - 1]
              if (lastMsg.role === 'assistant') {
                newMessages[newMessages.length - 1] = {
                  ...lastMsg,
                  content: lastMsg.content + (event.content || ''),
                }
              }
              return newMessages
            })
            break

          case 'action':
            setPendingAction(event.action || null)
            break

          case 'done':
            setLoading(false)
            setStreaming(false)
            if (event.response) {
              setMessages((prev) => {
                const newMessages = [...prev]
                const lastMsg = newMessages[newMessages.length - 1]
                if (lastMsg.role === 'assistant') {
                  newMessages[newMessages.length - 1] = {
                    ...lastMsg,
                    content: event.response || lastMsg.content,
                  }
                }
                return newMessages
              })
            }
            break

          case 'error':
            setMessages((prev) => {
              const newMessages = [...prev]
              const lastMsg = newMessages[newMessages.length - 1]
              if (lastMsg.role === 'assistant') {
                newMessages[newMessages.length - 1] = {
                  ...lastMsg,
                  content: `抱歉，发生错误：${event.message}`,
                }
              }
              return newMessages
            })
            setLoading(false)
            setStreaming(false)
            break
        }
      },
      (error) => {
        setMessages((prev) => {
          const newMessages = [...prev]
          const lastMsg = newMessages[newMessages.length - 1]
          if (lastMsg.role === 'assistant') {
            newMessages[newMessages.length - 1] = {
              ...lastMsg,
              content: '抱歉，操作失败，请稍后再试。',
            }
          }
          return newMessages
        })
        setLoading(false)
        setStreaming(false)
      },
    )
  }, [threadId, pendingAction, messages, addThinkingStep])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleClear = () => {
    abortRef.current?.()
    setMessages([WELCOME_MESSAGE])
    setThreadId(null)
    setPendingAction(null)
    setThinkingSteps([])
    setLoading(false)
    setStreaming(false)
  }

  const renderMessage = (msg: ChatMessage, index: number) => {
    const isUser = msg.role === 'user'
    const isLast = index === messages.length - 1
    const isStreamingMsg = isLast && streaming && !isUser

    return (
      <div key={index} className={`${styles.messageRow} ${isUser ? styles.userRow : styles.assistantRow}`}>
        {!isUser && <Avatar icon={<RobotOutlined />} className={`${styles.avatar} ${styles.assistantAvatar}`} />}
        <div className={`${styles.bubble} ${isUser ? styles.userBubble : styles.assistantBubble}`}>
          {isUser ? (
            <Typography.Text style={{ color: '#fff', whiteSpace: 'pre-wrap' }}>{msg.content}</Typography.Text>
          ) : (
            <div className="markdown-body">
              {msg.content ? (
                <ReactMarkdown>{msg.content}</ReactMarkdown>
              ) : isStreamingMsg ? (
                <Text type="secondary">思考中...</Text>
              ) : null}
              {isStreamingMsg && msg.content && <span className={styles.cursor}>▋</span>}
            </div>
          )}
        </div>
        {isUser && <Avatar icon={<UserOutlined />} className={`${styles.avatar} ${styles.userAvatar}`} />}
      </div>
    )
  }

  return (
    <div className={styles.container}>
      <Card
        title="Agent对话"
        extra={
          <Space>
            {streaming && <Tag color="processing">处理中...</Tag>}
            <Button icon={<ClearOutlined />} onClick={handleClear} size="small">
              清空对话
            </Button>
          </Space>
        }
        className={styles.card}
        styles={{ body: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: 0 } }}
      >
        <div className={styles.messagesArea}>
          {messages.map((msg, i) => {
            const isLast = i === messages.length - 1
            const isAssistant = msg.role === 'assistant'
            return (
              <React.Fragment key={i}>
                {/* 在最后一条助手消息前显示思考过程 */}
                {isLast && isAssistant && thinkingSteps.length > 0 && (
                  <ThinkingProcess steps={thinkingSteps} />
                )}
                {renderMessage(msg, i)}
              </React.Fragment>
            )
          })}
          <div ref={messagesEndRef} />
        </div>

        {/* Action confirmation bar */}
        {pendingAction && (
          <div className={styles.actionBar}>
            <div className={styles.actionContent}>
              <Typography.Text strong>Agent 请求执行操作：</Typography.Text>
              <Typography.Text code>{pendingAction.tool}</Typography.Text>
              {pendingAction.message && (
                <Typography.Paragraph type="secondary" style={{ margin: '4px 0 8px' }}>
                  {pendingAction.message}
                </Typography.Paragraph>
              )}
            </div>
            <Space>
              <Button
                type="primary"
                icon={<CheckOutlined />}
                onClick={() => handleResume(true)}
                loading={loading}
              >
                确认执行
              </Button>
              <Button
                danger
                icon={<CloseOutlined />}
                onClick={() => handleResume(false)}
                disabled={loading}
              >
                取消
              </Button>
            </Space>
          </div>
        )}

        <div className={styles.inputArea}>
          <Space.Compact style={{ width: '100%' }}>
            <TextArea
              ref={inputRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入消息... (Enter发送, Shift+Enter换行)"
              autoSize={{ minRows: 1, maxRows: 4 }}
              disabled={loading || !!pendingAction}
              style={{ flex: 1 }}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSend}
              loading={loading && !pendingAction}
              disabled={!inputValue.trim() || !!pendingAction}
              style={{ height: 'auto' }}
            >
              发送
            </Button>
          </Space.Compact>
        </div>
      </Card>
    </div>
  )
}
