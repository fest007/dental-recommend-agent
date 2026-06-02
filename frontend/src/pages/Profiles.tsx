import React, { useEffect, useState, useCallback } from 'react'
import {
  Table,
  Button,
  Card,
  Descriptions,
  Modal,
  Typography,
  message,
  Space,
  Tag,
  Spin,
  Empty,
  Tooltip,
} from 'antd'
import {
  EyeOutlined,
  ReloadOutlined,
  UserOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { getProfiles, getProfile, computeProfile, type UserProfile } from '../services/api'
import styles from './Profiles.module.less'

// 字段中文映射
const FIELD_LABELS: Record<string, string> = {
  // 顶层字段
  user_id: '用户ID',
  profile_generated_at: '画像生成时间',
  basic_info: '基本信息',
  purchase_summary: '采购汇总',
  category_preference: '品类偏好',
  brand_preference: '品牌偏好',
  purchase_cycle: '采购周期',
  consumable_alerts: '补货提醒',
  recency_score: '最近活跃度',
  value_tier: '价值层级',

  // basic_info 内部字段
  customer_type: '客户类型',
  purchase_span_days: '采购时间跨度（天）',
  first_purchase_date: '首次采购日期',
  last_purchase_date: '最近采购日期',

  // purchase_summary 内部字段
  total_records: '总采购记录数',
  unique_skus: '采购商品种类数',
  purchase_dates: '采购天数',
  avg_records_per_date: '平均每天采购数',

  // category_preference / brand_preference 内部字段
  category: '品类',
  brand: '品牌',
  count: '数量',
  ratio: '占比',

  // purchase_cycle 内部字段
  avg_days: '平均采购间隔（天）',

  // consumable_alerts 内部字段
  product_name: '商品名称',
  sku: 'SKU编号',
  related_device: '关联设备',
  last_purchased: '上次采购日期',
  expected_replacement: '预计更换日期',
  days_overdue: '超期天数',
  status: '状态',

  // 其他可能的字段
  quantity: '数量',
  purchase_date: '采购日期',
}

// 价值层级中文映射
const VALUE_TIER_LABELS: Record<string, string> = {
  high: '高价值客户',
  medium: '中等价值客户',
  low: '低价值客户',
}

// 补货状态中文映射
const ALERT_STATUS_LABELS: Record<string, { text: string; color: string }> = {
  overdue: { text: '已超期', color: 'red' },
  upcoming: { text: '即将到期', color: 'orange' },
}

// 获取字段中文标签
const getFieldLabel = (key: string): string => {
  return FIELD_LABELS[key] || key
}

// 格式化占比
const formatRatio = (ratio: number): string => {
  return `${Math.round(ratio * 100)}%`
}

// 格式化日期
const formatDate = (dateStr: string | null | undefined): string => {
  if (!dateStr) return '-'
  try {
    return new Date(dateStr).toLocaleDateString('zh-CN')
  } catch {
    return dateStr
  }
}

export default function Profiles() {
  const [data, setData] = useState<UserProfile[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(false)

  // Detail modal
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailItem, setDetailItem] = useState<UserProfile | null>(null)
  const [computing, setComputing] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getProfiles({ page, page_size: pageSize })
      setData(res.data.items)
      setTotal(res.data.total)
    } catch {
      message.error('加载用户画像失败')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleViewDetail = async (record: UserProfile) => {
    setDetailLoading(true)
    setDetailOpen(true)
    try {
      const res = await getProfile(record.user_id)
      setDetailItem(res.data)
    } catch {
      message.error('加载画像详情失败')
      setDetailItem(record)
    } finally {
      setDetailLoading(false)
    }
  }

  const handleCompute = async (userId: string) => {
    setComputing(userId)
    try {
      await computeProfile(userId)
      message.success(`用户 ${userId} 画像已重新计算`)
      fetchData()
    } catch {
      message.error('重新计算失败')
    } finally {
      setComputing(null)
    }
  }

  const renderProfileJson = (profileJson: Record<string, unknown>) => {
    if (!profileJson || Object.keys(profileJson).length === 0) {
      return <Empty description="暂无画像数据" />
    }

    // 价值层级颜色映射
    const valueTierColorMap: Record<string, string> = {
      high: 'red',
      medium: 'orange',
      low: 'green',
    }

    return (
      <div>
        {/* 顶部摘要信息 */}
        <Descriptions column={2} bordered size="small" style={{ marginBottom: 16 }}>
          {'recency_score' in profileJson ? (
            <Descriptions.Item
              label={
                <Space>
                  {getFieldLabel('recency_score')}
                  <Tooltip title="根据最近采购时间计算的活跃度分数，1.0表示最近有采购，0.0表示超过1年无采购">
                    <QuestionCircleOutlined style={{ color: '#999' }} />
                  </Tooltip>
                </Space>
              }
            >
              <Typography.Text strong>
                {Math.round((profileJson.recency_score as number) * 100)}%
              </Typography.Text>
            </Descriptions.Item>
          ) : null}
          {'value_tier' in profileJson ? (
            <Descriptions.Item label={getFieldLabel('value_tier')}>
              <Tag color={valueTierColorMap[profileJson.value_tier as string] || 'default'}>
                {VALUE_TIER_LABELS[profileJson.value_tier as string] || String(profileJson.value_tier)}
              </Tag>
            </Descriptions.Item>
          ) : null}
        </Descriptions>

        {/* 基本信息 */}
        {profileJson.basic_info && typeof profileJson.basic_info === 'object' ? (
          <Card
            title={getFieldLabel('basic_info')}
            size="small"
            style={{ marginBottom: 16 }}
          >
            <Descriptions column={2} bordered size="small">
              {Object.entries(profileJson.basic_info as Record<string, unknown>).map(([k, v]) => (
                <Descriptions.Item key={k} label={getFieldLabel(k)}>
                  {k.includes('date') ? formatDate(v as string) : String(v ?? '-')}
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
        ) : null}

        {/* 采购汇总 */}
        {profileJson.purchase_summary && typeof profileJson.purchase_summary === 'object' ? (
          <Card
            title={getFieldLabel('purchase_summary')}
            size="small"
            style={{ marginBottom: 16 }}
          >
            <Descriptions column={2} bordered size="small">
              {Object.entries(profileJson.purchase_summary as Record<string, unknown>).map(([k, v]) => (
                <Descriptions.Item key={k} label={getFieldLabel(k)}>
                  {String(v ?? '-')}
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
        ) : null}

        {/* 品类偏好 */}
        {Array.isArray(profileJson.category_preference) && profileJson.category_preference.length > 0 ? (
          <Card
            title={getFieldLabel('category_preference')}
            size="small"
            style={{ marginBottom: 16 }}
          >
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {(profileJson.category_preference as Array<Record<string, unknown>>).map((item, idx) => {
                const category = String(item.category || '-')
                const count = item.count ? Number(item.count) : 0
                const ratio = item.ratio ? Number(item.ratio) : 0
                return (
                  <Tag key={idx} color="blue" style={{ marginBottom: 4 }}>
                    {category}
                    {count > 0 ? ` (${count}次)` : ''}
                    {ratio > 0 ? ` ${formatRatio(ratio)}` : ''}
                  </Tag>
                )
              })}
            </div>
          </Card>
        ) : null}

        {/* 品牌偏好 */}
        {Array.isArray(profileJson.brand_preference) && profileJson.brand_preference.length > 0 ? (
          <Card
            title={getFieldLabel('brand_preference')}
            size="small"
            style={{ marginBottom: 16 }}
          >
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {(profileJson.brand_preference as Array<Record<string, unknown>>).map((item, idx) => {
                const brand = String(item.brand || '-')
                const count = item.count ? Number(item.count) : 0
                const ratio = item.ratio ? Number(item.ratio) : 0
                return (
                  <Tag key={idx} color="green" style={{ marginBottom: 4 }}>
                    {brand}
                    {count > 0 ? ` (${count}次)` : ''}
                    {ratio > 0 ? ` ${formatRatio(ratio)}` : ''}
                  </Tag>
                )
              })}
            </div>
          </Card>
        ) : null}

        {/* 采购周期 */}
        {profileJson.purchase_cycle && typeof profileJson.purchase_cycle === 'object' && Object.keys(profileJson.purchase_cycle).length > 0 ? (
          <Card
            title={
              <Space>
                {getFieldLabel('purchase_cycle')}
                <Tooltip title="统计各品类的平均采购间隔，帮助预测下次采购时间">
                  <QuestionCircleOutlined style={{ color: '#999' }} />
                </Tooltip>
              </Space>
            }
            size="small"
            style={{ marginBottom: 16 }}
          >
            {Object.entries(profileJson.purchase_cycle as Record<string, Record<string, unknown>>).map(([cat, info]) => (
              <Card
                key={cat}
                size="small"
                style={{ marginBottom: 8, background: '#fafafa' }}
              >
                <Typography.Text strong>{cat}</Typography.Text>
                <Descriptions column={3} size="small" style={{ marginTop: 8 }}>
                  <Descriptions.Item label={getFieldLabel('avg_days')}>
                    {info.avg_days ? `${info.avg_days} 天` : '数据不足'}
                  </Descriptions.Item>
                  <Descriptions.Item label={getFieldLabel('last_purchase_date')}>
                    {formatDate(info.last_purchase_date as string)}
                  </Descriptions.Item>
                  <Descriptions.Item label={getFieldLabel('count')}>
                    {String(info.count)} 次
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ))}
          </Card>
        ) : null}

        {/* 补货提醒 */}
        {Array.isArray(profileJson.consumable_alerts) && profileJson.consumable_alerts.length > 0 ? (
          <Card
            title={
              <Space>
                {getFieldLabel('consumable_alerts')}
                <Tooltip title="根据商品的典型采购周期，提醒需要补货的耗材/配件">
                  <QuestionCircleOutlined style={{ color: '#999' }} />
                </Tooltip>
              </Space>
            }
            size="small"
            style={{ marginBottom: 16 }}
          >
            {(profileJson.consumable_alerts as Array<Record<string, unknown>>).map((alert, idx) => {
              const statusInfo = ALERT_STATUS_LABELS[alert.status as string] || { text: String(alert.status), color: 'default' }
              return (
                <Card
                  key={idx}
                  size="small"
                  style={{ marginBottom: 8, background: '#fffbe6' }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <Typography.Text strong>{String(alert.product_name || '-')}</Typography.Text>
                      <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                        SKU: {String(alert.sku || '-')}
                      </Typography.Text>
                    </div>
                    <Tag color={statusInfo.color}>{statusInfo.text}</Tag>
                  </div>
                  <Descriptions column={2} size="small" style={{ marginTop: 8 }}>
                    <Descriptions.Item label={getFieldLabel('related_device')}>
                      {String(alert.related_device || '-')}
                    </Descriptions.Item>
                    <Descriptions.Item label={getFieldLabel('last_purchased')}>
                      {formatDate(alert.last_purchased as string)}
                    </Descriptions.Item>
                    <Descriptions.Item label={getFieldLabel('expected_replacement')}>
                      {formatDate(alert.expected_replacement as string)}
                    </Descriptions.Item>
                    <Descriptions.Item label={getFieldLabel('days_overdue')}>
                      {(alert.days_overdue as number) > 0 ? (
                        <Typography.Text type="danger">{String(alert.days_overdue)} 天</Typography.Text>
                      ) : (
                        <Typography.Text>未超期</Typography.Text>
                      )}
                    </Descriptions.Item>
                  </Descriptions>
                </Card>
              )
            })}
          </Card>
        ) : null}

        {/* 其他未分类字段 */}
        {(() => {
          const knownKeys = new Set([
            'basic_info', 'category_preference', 'brand_preference',
            'purchase_cycle', 'consumable_alerts', 'recency_score', 'value_tier',
            'purchase_summary', 'user_id', 'profile_generated_at',
          ])
          const remaining = Object.entries(profileJson).filter(([k]) => !knownKeys.has(k))
          if (remaining.length === 0) return null
          return (
            <Card title="其他信息" size="small">
              <Descriptions column={1} bordered size="small">
                {remaining.map(([k, v]) => (
                  <Descriptions.Item key={k} label={getFieldLabel(k)}>
                    {typeof v === 'object' ? JSON.stringify(v) : String(v ?? '-')}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            </Card>
          )
        })()}
      </div>
    )
  }

  const columns: ColumnsType<UserProfile> = [
    {
      title: '用户ID',
      dataIndex: 'user_id',
      key: 'user_id',
      width: 150,
      render: (v: string) => (
        <Space>
          <UserOutlined />
          {v}
        </Space>
      ),
    },
    {
      title: '价值层级',
      key: 'value_tier',
      width: 120,
      render: (_, record) => {
        const tier = record.profile_json?.value_tier as string | undefined
        if (!tier) return '-'
        const colorMap: Record<string, string> = {
          high: 'red',
          medium: 'orange',
          low: 'green',
        }
        return (
          <Tag color={colorMap[tier] || 'default'}>
            {VALUE_TIER_LABELS[tier] || tier}
          </Tag>
        )
      },
    },
    {
      title: '品类数',
      key: 'category_count',
      width: 80,
      render: (_, record) => {
        const prefs = record.profile_json?.category_preference
        return Array.isArray(prefs) ? prefs.length : '-'
      },
    },
    {
      title: '品牌数',
      key: 'brand_count',
      width: 80,
      render: (_, record) => {
        const prefs = record.profile_json?.brand_preference
        return Array.isArray(prefs) ? prefs.length : '-'
      },
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_, record) => (
        <Space>
          <Button
            type="link"
            icon={<EyeOutlined />}
            onClick={() => handleViewDetail(record)}
            size="small"
          >
            查看画像
          </Button>
          <Button
            type="link"
            icon={<ReloadOutlined />}
            onClick={() => handleCompute(record.user_id)}
            loading={computing === record.user_id}
            size="small"
          >
            重新计算
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div className={styles.container}>
      <Typography.Title level={3}>用户画像管理</Typography.Title>

      <Card>
        <Table<UserProfile>
          rowKey="id"
          columns={columns}
          dataSource={data}
          loading={loading}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p)
              setPageSize(ps)
            },
          }}
          scroll={{ x: 800 }}
        />
      </Card>

      {/* Detail Modal */}
      <Modal
        title={`用户画像 - ${detailItem?.user_id || ''}`}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        width={800}
      >
        <Spin spinning={detailLoading}>
          {detailItem ? renderProfileJson(detailItem.profile_json) : <Empty />}
        </Spin>
      </Modal>
    </div>
  )
}
