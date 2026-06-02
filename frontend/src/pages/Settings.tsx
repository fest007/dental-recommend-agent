import React, { useEffect, useState } from 'react'
import {
  Form,
  Input,
  Select,
  InputNumber,
  Button,
  Card,
  Collapse,
  Alert,
  Switch,
  Space,
  Divider,
  message,
  Spin,
  Tag,
  Typography,
} from 'antd'
import {
  SaveOutlined,
  ReloadOutlined,
  ApiOutlined,
  CloudSyncOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons'
import {
  getLlmConfig,
  saveLlmConfig,
  testConnection,
  getModels,
  type LlmConfig,
} from '../services/api'
import styles from './Settings.module.less'

const { Panel } = Collapse

interface FormValues {
  base_url: string
  api_key: string
  ranking_model: string
  enrichment_model: string
  embedding_model: string
  temperature: number
  max_tokens: number
  timeout: number
  langsmith_api_key: string
  langsmith_project: string
  langsmith_enabled: boolean
}

interface ConnectionStatus {
  status: 'untested' | 'success' | 'error'
  lastTestTime: string | null
  modelCount: number
  error?: string
}

export default function Settings() {
  const [form] = Form.useForm<FormValues>()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [fetchingModels, setFetchingModels] = useState(false)
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>({
    status: 'untested',
    lastTestTime: null,
    modelCount: 0,
  })

  // Load current config on mount
  useEffect(() => {
    loadConfig()
  }, [])

  const loadConfig = async () => {
    setLoading(true)
    try {
      const res = await getLlmConfig()
      const cfg = res.data
      form.setFieldsValue({
        base_url: cfg.base_url,
        api_key: cfg.api_key,
        ranking_model: cfg.ranking_model,
        enrichment_model: cfg.enrichment_model,
        embedding_model: cfg.embedding_model,
        temperature: cfg.temperature,
        max_tokens: cfg.max_tokens,
        timeout: cfg.timeout,
        langsmith_api_key: cfg.langsmith_api_key || '',
        langsmith_project: cfg.langsmith_project || 'dental-recommend-agent',
        langsmith_enabled: cfg.langsmith_enabled || false,
      })
      // Try to load cached models and connection status
      try {
        const modelsRes = await getModels()
        if (modelsRes.data.models && modelsRes.data.models.length > 0) {
          setModelOptions(modelsRes.data.models)
          setConnectionStatus({
            status: 'success',
            lastTestTime: modelsRes.data.updated_at || null,
            modelCount: modelsRes.data.models.length,
          })
        }
      } catch {
        // Ignore model fetch errors on load
      }
    } catch {
      message.error('加载LLM配置失败')
    } finally {
      setLoading(false)
    }
  }

  const handleTestConnection = async () => {
    const base_url = form.getFieldValue('base_url')
    const api_key = form.getFieldValue('api_key')

    if (!base_url || !api_key) {
      message.warning('请先填写 Base URL 和 API Key')
      return
    }

    setTesting(true)
    try {
      const res = await testConnection(base_url, api_key)
      if (res.data.success) {
        message.success('连接测试成功')
        setConnectionStatus({
          status: 'success',
          lastTestTime: new Date().toISOString(),
          modelCount: res.data.models.length,
        })
      } else {
        message.error(`连接测试失败: ${res.data.error || '未知错误'}`)
        setConnectionStatus({
          status: 'error',
          lastTestTime: new Date().toISOString(),
          modelCount: 0,
          error: res.data.error || undefined,
        })
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : '连接测试失败'
      message.error(errMsg)
      setConnectionStatus({
        status: 'error',
        lastTestTime: new Date().toISOString(),
        modelCount: 0,
        error: errMsg,
      })
    } finally {
      setTesting(false)
    }
  }

  const handleGetModels = async () => {
    const base_url = form.getFieldValue('base_url')
    const api_key = form.getFieldValue('api_key')

    if (!base_url || !api_key) {
      message.warning('请先填写 Base URL 和 API Key')
      return
    }

    setFetchingModels(true)
    try {
      const res = await testConnection(base_url, api_key)
      if (res.data.success && res.data.models.length > 0) {
        setModelOptions(res.data.models)
        setConnectionStatus({
          status: 'success',
          lastTestTime: new Date().toISOString(),
          modelCount: res.data.models.length,
        })
        message.success(`获取到 ${res.data.models.length} 个模型`)
      } else {
        message.error(`获取模型列表失败: ${res.data.error || '未返回模型列表'}`)
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : '获取模型列表失败'
      message.error(errMsg)
    } finally {
      setFetchingModels(false)
    }
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      await saveLlmConfig(values)
      message.success('配置保存成功')
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) {
        // Form validation error
        message.warning('请检查表单填写是否完整')
      } else {
        const errMsg = err instanceof Error ? err.message : '保存配置失败'
        message.error(errMsg)
      }
    } finally {
      setSaving(false)
    }
  }

  const handleReset = () => {
    loadConfig()
    message.info('已重置为已保存的配置')
  }

  const renderStatusTag = () => {
    switch (connectionStatus.status) {
      case 'success':
        return (
          <Tag icon={<CheckCircleOutlined />} color="success">
            已连接
          </Tag>
        )
      case 'error':
        return (
          <Tag icon={<CloseCircleOutlined />} color="error">
            连接失败
          </Tag>
        )
      default:
        return (
          <Tag icon={<QuestionCircleOutlined />} color="default">
            未测试
          </Tag>
        )
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.container}>
        <Spin spinning={loading} className={styles.spin}>
          <div className={styles.contentInner}>
        <Typography.Title level={3}>LLM 配置</Typography.Title>

        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          {/* Main config form */}
          <Card title="连接配置" style={{ flex: 1, minWidth: 500 }}>
            <Form
              form={form}
              layout="vertical"
              initialValues={{
                temperature: 0.7,
                max_tokens: 4096,
                timeout: 30,
              }}
            >
              <Form.Item
                name="base_url"
                label="Base URL"
                rules={[{ required: true, message: '请输入Base URL' }]}
              >
                <Input placeholder="https://api.openai.com/v1" />
              </Form.Item>

              <Form.Item
                name="api_key"
                label="API Key"
                rules={[{ required: true, message: '请输入API Key' }]}
              >
                <Input.Password
                  placeholder="sk-..."
                  visibilityToggle
                />
              </Form.Item>

              <Space style={{ marginBottom: 24 }}>
                <Button
                  icon={<ApiOutlined />}
                  onClick={handleTestConnection}
                  loading={testing}
                >
                  测试连接
                </Button>
                <Button
                  icon={<CloudSyncOutlined />}
                  onClick={handleGetModels}
                  loading={fetchingModels}
                >
                  获取模型列表
                </Button>
              </Space>

              <Divider />

              <Form.Item
                name="ranking_model"
                label="推荐/对话模型"
                rules={[{ required: true, message: '请选择推荐/对话模型' }]}
              >
                <Select
                  showSearch
                  allowClear
                  placeholder="选择或输入模型名称"
                  options={modelOptions.map((m) => ({ label: m, value: m }))}
                  filterOption={(input, option) =>
                    (option?.label ?? '').toString().toLowerCase().includes(input.toLowerCase())
                  }
                  notFoundContent={modelOptions.length === 0 ? '请先获取模型列表' : '无匹配模型'}
                />
              </Form.Item>

              <Form.Item
                name="enrichment_model"
                label="增强/批量模型"
                rules={[{ required: true, message: '请选择增强/批量模型' }]}
              >
                <Select
                  showSearch
                  allowClear
                  placeholder="选择或输入模型名称"
                  options={modelOptions.map((m) => ({ label: m, value: m }))}
                  filterOption={(input, option) =>
                    (option?.label ?? '').toString().toLowerCase().includes(input.toLowerCase())
                  }
                  notFoundContent={modelOptions.length === 0 ? '请先获取模型列表' : '无匹配模型'}
                />
              </Form.Item>

              <Form.Item
                name="embedding_model"
                label="Embedding模型"
                rules={[{ required: true, message: '请选择Embedding模型' }]}
              >
                <Select
                  showSearch
                  allowClear
                  placeholder="选择或输入模型名称"
                  options={modelOptions.map((m) => ({ label: m, value: m }))}
                  filterOption={(input, option) =>
                    (option?.label ?? '').toString().toLowerCase().includes(input.toLowerCase())
                  }
                  notFoundContent={modelOptions.length === 0 ? '请先获取模型列表' : '无匹配模型'}
                />
              </Form.Item>
            </Form>
          </Card>

          {/* Status panel */}
          <Card title="连接状态" style={{ width: 300 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <div>
                <Typography.Text type="secondary">连接状态：</Typography.Text>
                <div style={{ marginTop: 4 }}>{renderStatusTag()}</div>
              </div>
              <div>
                <Typography.Text type="secondary">上次测试时间：</Typography.Text>
                <div style={{ marginTop: 4 }}>
                  {connectionStatus.lastTestTime
                    ? new Date(connectionStatus.lastTestTime).toLocaleString('zh-CN')
                    : '-'}
                </div>
              </div>
              <div>
                <Typography.Text type="secondary">可用模型数量：</Typography.Text>
                <div style={{ marginTop: 4 }}>
                  <Tag color="blue">{connectionStatus.modelCount}</Tag>
                </div>
              </div>
              {connectionStatus.error && (
                <Alert
                  type="error"
                  message="错误信息"
                  description={connectionStatus.error}
                  showIcon
                  closable
                />
              )}
            </Space>
          </Card>
        </div>

        {/* Advanced settings */}
        <Collapse style={{ marginTop: 24 }}>
          <Panel header="高级设置" key="advanced">
            <Form form={form} layout="inline" style={{ flexWrap: 'wrap', gap: 16 }}>
              <Form.Item name="temperature" label="Temperature">
                <InputNumber min={0} max={2} step={0.1} style={{ width: 120 }} />
              </Form.Item>
              <Form.Item name="max_tokens" label="Max Tokens">
                <InputNumber min={1} max={128000} step={256} style={{ width: 150 }} />
              </Form.Item>
              <Form.Item name="timeout" label="超时时间(秒)">
                <InputNumber min={1} max={600} step={5} style={{ width: 120 }} />
              </Form.Item>
            </Form>
          </Panel>
        </Collapse>

        {/* LangSmith monitoring config */}
        <Collapse style={{ marginTop: 24 }}>
          <Panel header="LangSmith 监控配置" key="langsmith">
            <Form form={form} layout="vertical" style={{ maxWidth: 600 }}>
              <Form.Item
                name="langsmith_enabled"
                label="启用 LangSmith 监控"
                valuePropName="checked"
              >
                <Switch checkedChildren="开启" unCheckedChildren="关闭" />
              </Form.Item>
              <Form.Item
                name="langsmith_api_key"
                label="LangSmith API Key"
                extra="在 smith.langchain.com 获取 API Key"
              >
                <Input.Password placeholder="lsv2_pt-..." />
              </Form.Item>
              <Form.Item
                name="langsmith_project"
                label="项目名称"
                extra="用于在 LangSmith 中分组追踪数据"
              >
                <Input placeholder="dental-recommend-agent" />
              </Form.Item>
              <Alert
                message="LangSmith 用于监控 LLM 调用链路，追踪商品增强、推荐排序、Agent对话等操作的输入输出和耗时。"
                type="info"
                showIcon
              />
            </Form>
          </Panel>
        </Collapse>

        {/* Action buttons */}
        <Space style={{ marginTop: 24 }}>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            onClick={handleSave}
            loading={saving}
            size="large"
          >
            保存配置
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={handleReset}
            size="large"
          >
            重置
          </Button>
        </Space>
          </div>
        </Spin>
      </div>
    </div>
  )
}
