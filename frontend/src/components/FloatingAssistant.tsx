import React, { useState, useRef, useEffect } from 'react'
import { FloatButton, Modal, Input, Button, Space, Avatar, Spin, Typography, message } from 'antd'
import {
  RobotOutlined, SendOutlined, CloseOutlined, UserOutlined,
  ExclamationCircleOutlined, CheckCircleOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import { sendMessage, resumeChat, type ChatMessage, type ChatResponse } from '../services/api'
import styles from './FloatingAssistant.module.less'

const { TextArea } = Input
const { Text } = Typography

interface PendingAction {
  tool: string
  args: Record<string, unknown>
  message: string
}

const WELCOME_MESSAGE: ChatMessage = {
  role: 'assistant',
  content: `你好！我是工作助理，可以帮你：

- **添加购买记录**："帮我添加KH3734的购买记录，SKU:VZ008417，数量150"
- **删除购买记录**："删除KH3734最近买的一条VZ008417"
- **修改画像**："把KH3734的客户类型改为经销商"
- **生成推荐**："帮KH3734生成推荐"
- **查询信息**："KH3734的画像是什么？"`,
}

const WRITE_TOOLS = new Set(['add_purchase', 'delete_purchase', 'update_profile', 'generate_user_recommendations'])
const DELETE_TOOLS = new Set(['delete_purchase'])

export default function FloatingAssistant() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME_MESSAGE])
  const [threadId, setThreadId] = useState<string | null>(null)
  const [inputValue, setInputValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null)
  const [confirmVisible, setConfirmVisible] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 100)
  }, [open])

  const appendMessages = (newMsgs: ChatMessage[]) => {
    setMessages(prev => [...prev, ...newMsgs])
  }

  const handleAgentResponse = (data: ChatResponse) => {
    setThreadId(data.thread_id)
    if (data.response) {
      appendMessages([{ role: 'assistant', content: data.response }])
    }
    if (data.action && data.action.type === 'confirm_tool_call' && WRITE_TOOLS.has(data.action.tool)) {
      setPendingAction({ tool: data.action.tool, args: data.action.args, message: data.action.message })
      setConfirmVisible(true)
    }
  }

  const handleSend = async () => {
    const text = inputValue.trim()
    if (!text || loading) return
    appendMessages([{ role: 'user', content: text }])
    setInputValue('')
    setLoading(true)
    try {
      const res = await sendMessage(text, threadId)
      handleAgentResponse(res.data)
    } catch {
      appendMessages([{ role: 'assistant', content: '抱歉，发送消息失败，请稍后再试。' }])
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  const handleConfirm = async (approved: boolean) => {
    if (!threadId) return
    setConfirmVisible(false)
    setLoading(true)
    appendMessages([{ role: 'user', content: approved ? '确认执行' : '取消' }])
    try {
      const res = await resumeChat(threadId, approved)
      handleAgentResponse(res.data)
    } catch {
      appendMessages([{ role: 'assistant', content: approved ? '操作执行失败。' : '取消失败。' }])
    } finally {
      setLoading(false)
      setPendingAction(null)
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleClear = () => {
    setMessages([WELCOME_MESSAGE])
    setPendingAction(null)
    setThreadId(null)
  }

  const formatToolName = (tool: string) => {
    const map: Record<string, string> = {
      add_purchase: '添加购买记录', delete_purchase: '删除购买记录',
      update_profile: '修改用户画像', generate_user_recommendations: '生成推荐',
    }
    return map[tool] || tool
  }

  const renderMessage = (msg: ChatMessage, index: number) => {
    const isUser = msg.role === 'user'
    return (
      <div key={index} className={`${styles.messageRow} ${isUser ? styles.userRow : styles.assistantRow}`}>
        {!isUser && <Avatar icon={<RobotOutlined />} size="small" className={`${styles.avatar} ${styles.assistantAvatar}`} />}
        <div className={`${styles.bubble} ${isUser ? styles.userBubble : styles.assistantBubble}`}>
          {isUser ? msg.content : (
            <ReactMarkdown components={{
              p: ({ children }) => <p style={{ margin: '4px 0' }}>{children}</p>,
              ul: ({ children }) => <ul style={{ margin: '4px 0', paddingLeft: 16 }}>{children}</ul>,
              ol: ({ children }) => <ol style={{ margin: '4px 0', paddingLeft: 16 }}>{children}</ol>,
              li: ({ children }) => <li style={{ margin: '2px 0' }}>{children}</li>,
              code: ({ children }) => <code style={{ backgroundColor: '#e8e8e8', padding: '1px 4px', borderRadius: 3, fontSize: 12 }}>{children}</code>,
            }}>{msg.content}</ReactMarkdown>
          )}
        </div>
        {isUser && <Avatar icon={<UserOutlined />} size="small" className={`${styles.avatar} ${styles.userAvatar}`} />}
      </div>
    )
  }

  return (
    <>
      <FloatButton.Group shape="circle" className={styles.floatButton}>
        <FloatButton icon={<RobotOutlined />} type="primary" onClick={() => setOpen(true)} tooltip="工作助理" />
      </FloatButton.Group>

      <Modal
        title={<Space><RobotOutlined style={{ color: '#1890ff' }} /><span>工作助理</span></Space>}
        open={open} onCancel={() => setOpen(false)} footer={null} width={480}
        className={styles.modal} closeIcon={<CloseOutlined />}
      >
        <div className={styles.messagesArea}>
          {messages.map(renderMessage)}
          {loading && <div style={{ padding: '0 12px', marginBottom: 12 }}><Spin size="small" /></div>}
          <div ref={messagesEndRef} />
        </div>
        <div className={styles.inputArea}>
          <TextArea ref={inputRef} value={inputValue} onChange={e => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown} placeholder="输入指令..."
            autoSize={{ minRows: 1, maxRows: 3 }} disabled={loading} />
          <Space direction="vertical" size={4}>
            <Button type="primary" icon={<SendOutlined />} onClick={handleSend} disabled={!inputValue.trim() || loading} size="small" />
            <Button icon={<CloseOutlined />} onClick={handleClear} size="small" title="清空对话" />
          </Space>
        </div>
      </Modal>

      <Modal
        title={
          <Space>
            {DELETE_TOOLS.has(pendingAction?.tool || '') ? <ExclamationCircleOutlined style={{ color: '#ff4d4f' }} /> : <CheckCircleOutlined style={{ color: '#52c41a' }} />}
            <span>操作确认</span>
          </Space>
        }
        open={confirmVisible} onOk={() => handleConfirm(true)} onCancel={() => handleConfirm(false)}
        okText="确认执行" cancelText="取消"
        okButtonProps={{ danger: DELETE_TOOLS.has(pendingAction?.tool || ''), loading }}
        cancelButtonProps={{ disabled: loading }}
      >
        <div className={styles.confirmBody}>
          {DELETE_TOOLS.has(pendingAction?.tool || '') && (
            <div className={styles.dangerWarning}>
              <Text type="danger" strong>⚠️ 删除操作不可恢复</Text>
            </div>
          )}
          <div style={{ marginBottom: 8, fontWeight: 500 }}>{formatToolName(pendingAction?.tool || '')}</div>
          <div style={{ fontSize: 13, color: '#666', whiteSpace: 'pre-wrap' }}>{pendingAction?.message || '确认执行此操作？'}</div>
          {pendingAction?.args && (
            <pre className={styles.argsPreview}>{JSON.stringify(pendingAction.args, null, 2)}</pre>
          )}
        </div>
      </Modal>
    </>
  )
}
