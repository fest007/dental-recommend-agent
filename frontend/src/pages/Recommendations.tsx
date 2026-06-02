import React, { useEffect, useState, useCallback } from 'react'
import {
  Table,
  Button,
  Input,
  Space,
  Card,
  Typography,
  message,
  Tag,
  Popconfirm,
  Progress,
  Modal,
  Statistic,
  Row,
  Col,
} from 'antd'
import {
  SearchOutlined,
  ThunderboltOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  RocketOutlined,
  EyeOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  getRecommendations,
  generateRecommendations,
  generateAllRecommendations,
  updateFeedback,
  type Recommendation,
} from '../services/api'
import styles from './Recommendations.module.less'

export default function Recommendations() {
  const [data, setData] = useState<Recommendation[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(false)
  const [userIdFilter, setUserIdFilter] = useState('')
  const [generatingUser, setGeneratingUser] = useState<string | null>(null)
  const [generatingAll, setGeneratingAll] = useState(false)
  const [selectedUsers, setSelectedUsers] = useState<string[]>([])
  const [batchGenerating, setBatchGenerating] = useState(false)

  // Generate-all result modal
  const [genAllResult, setGenAllResult] = useState<{
    message: string
    results: { user_id: string; count: number }[]
    errors: { user_id: string; error: string }[]
  } | null>(null)
  const [genAllModalOpen, setGenAllModalOpen] = useState(false)

  // Detail modal
  const [detailRecord, setDetailRecord] = useState<Recommendation | null>(null)
  const [detailModalOpen, setDetailModalOpen] = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getRecommendations({
        page,
        page_size: pageSize,
        user_id: userIdFilter || undefined,
      })
      setData(res.data.items)
      setTotal(res.data.total)
    } catch {
      message.error('加载推荐结果失败')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, userIdFilter])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleSearch = (value: string) => {
    setUserIdFilter(value)
    setPage(1)
  }

  const handleGenerateForUser = async (userId: string) => {
    setGeneratingUser(userId)
    try {
      const res = await generateRecommendations(userId)
      message.success(res.data.message)
      fetchData()
    } catch {
      message.error('生成推荐失败')
    } finally {
      setGeneratingUser(null)
    }
  }

  const handleGenerateAll = async () => {
    setGeneratingAll(true)
    try {
      const res = await generateAllRecommendations()
      setGenAllResult(res.data)
      setGenAllModalOpen(true)
      message.success(res.data.message)
      fetchData()
    } catch {
      message.error('批量生成推荐失败')
    } finally {
      setGeneratingAll(false)
    }
  }

  const handleBatchGenerate = async () => {
    if (selectedUsers.length === 0) {
      message.warning('请先勾选用户')
      return
    }
    setBatchGenerating(true)
    let success = 0
    let failed = 0
    for (const uid of selectedUsers) {
      try {
        await generateRecommendations(uid)
        success++
      } catch {
        failed++
      }
    }
    setBatchGenerating(false)
    setSelectedUsers([])
    message.success(`批量生成完成: 成功 ${success}, 失败 ${failed}`)
    fetchData()
  }

  const handleFeedback = async (id: number, status: 'accepted' | 'rejected') => {
    try {
      await updateFeedback(id, status)
      message.success(status === 'accepted' ? '已采纳' : '已拒绝')
      fetchData()
    } catch {
      message.error('反馈失败')
    }
  }

  const columns: ColumnsType<Recommendation> = [
    {
      title: '用户ID',
      dataIndex: 'user_id',
      key: 'user_id',
      width: 120,
    },
    {
      title: '推荐商品SKU',
      dataIndex: 'recommended_sku',
      key: 'recommended_sku',
      width: 140,
      ellipsis: true,
    },
    {
      title: '商品名称',
      dataIndex: 'product_name',
      key: 'product_name',
      width: 200,
      ellipsis: true,
    },
    {
      title: '排名',
      dataIndex: 'rank',
      key: 'rank',
      width: 70,
      align: 'center',
      sorter: (a, b) => a.rank - b.rank,
      render: (v: number) => (
        <Tag color={v <= 3 ? 'gold' : 'default'}>{v}</Tag>
      ),
    },
    {
      title: '推荐理由',
      dataIndex: 'reason',
      key: 'reason',
      ellipsis: true,
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 120,
      sorter: (a, b) => a.confidence - b.confidence,
      render: (v: number) => (
        <Progress
          percent={Math.round(v * 100)}
          size="small"
          status={v >= 0.7 ? 'success' : v >= 0.4 ? 'normal' : 'exception'}
        />
      ),
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 80,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: string) => {
        const colorMap: Record<string, string> = {
          pending: 'default',
          accepted: 'green',
          rejected: 'red',
        }
        const labelMap: Record<string, string> = {
          pending: '待处理',
          accepted: '已采纳',
          rejected: '已拒绝',
        }
        return <Tag color={colorMap[status] || 'default'}>{labelMap[status] || status}</Tag>
      },
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
            size="small"
            onClick={() => {
              setDetailRecord(record)
              setDetailModalOpen(true)
            }}
          >
            详情
          </Button>
          {record.status === 'pending' && (
            <>
              <Popconfirm
                title="确认采纳此推荐?"
                onConfirm={() => handleFeedback(record.id, 'accepted')}
                okText="确认"
                cancelText="取消"
              >
                <Button
                  type="link"
                  icon={<CheckCircleOutlined />}
                  size="small"
                  style={{ color: '#52c41a' }}
                >
                  采纳
                </Button>
              </Popconfirm>
              <Popconfirm
                title="确认拒绝此推荐?"
                onConfirm={() => handleFeedback(record.id, 'rejected')}
                okText="确认"
                cancelText="取消"
              >
                <Button
                  type="link"
                  danger
                  icon={<CloseCircleOutlined />}
                  size="small"
                >
                  拒绝
                </Button>
              </Popconfirm>
            </>
          )}
          {record.status !== 'pending' && (
            <Typography.Text type="secondary">已处理</Typography.Text>
          )}
        </Space>
      ),
    },
  ]

  // Group data by user_id for the "generate per user" feature
  const uniqueUsers = Array.from(new Set(data.map((r) => r.user_id)))

  return (
    <div className={styles.container}>
      <Typography.Title level={3}>推荐结果管理</Typography.Title>

      {/* Search and actions */}
      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input.Search
            placeholder="按用户ID筛选"
            allowClear
            enterButton={<SearchOutlined />}
            onSearch={handleSearch}
            style={{ width: 300 }}
          />
          <Popconfirm
            title="确定为所有用户生成推荐？"
            description="将为所有有购买记录的用户重新计算推荐结果"
            onConfirm={handleGenerateAll}
            okText="确认生成"
            cancelText="取消"
          >
            <Button
              type="primary"
              icon={<RocketOutlined />}
              loading={generatingAll}
            >
              全部生成
            </Button>
          </Popconfirm>
        </Space>
      </Card>

      {/* Per-user generate buttons */}
      {uniqueUsers.length > 0 && (
        <Card
          title={
            <Space>
              <span>按用户生成推荐</span>
              {selectedUsers.length > 0 && (
                <Popconfirm
                  title={`确定为 ${selectedUsers.length} 个用户生成推荐？`}
                  onConfirm={handleBatchGenerate}
                >
                  <Button type="primary" size="small" loading={batchGenerating}>
                    批量生成 ({selectedUsers.length})
                  </Button>
                </Popconfirm>
              )}
            </Space>
          }
          size="small"
          style={{ marginBottom: 16 }}
        >
          <Space wrap>
            <Button
              size="small"
              type={selectedUsers.length === uniqueUsers.length ? 'primary' : 'default'}
              onClick={() => {
                if (selectedUsers.length === uniqueUsers.length) {
                  setSelectedUsers([])
                } else {
                  setSelectedUsers([...uniqueUsers])
                }
              }}
            >
              {selectedUsers.length === uniqueUsers.length ? '取消全选' : '全选'}
            </Button>
            {uniqueUsers.map((userId) => {
              const isSelected = selectedUsers.includes(userId)
              return (
                <Button
                  key={userId}
                  icon={<ThunderboltOutlined />}
                  onClick={() => handleGenerateForUser(userId)}
                  onDoubleClick={() => {
                    setSelectedUsers(
                      isSelected
                        ? selectedUsers.filter((u) => u !== userId)
                        : [...selectedUsers, userId],
                    )
                  }}
                  loading={generatingUser === userId}
                  size="small"
                  type={isSelected ? 'primary' : 'default'}
                  ghost={isSelected}
                >
                  {userId}
                </Button>
              )
            })}
          </Space>
          <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
            双击用户按钮可勾选/取消，勾选后点击"批量生成"
          </div>
        </Card>
      )}

      {/* Table */}
      <Card>
        <Table<Recommendation>
          rowKey="id"
          columns={columns}
          dataSource={data}
          loading={loading}
          onRow={(record) => ({
            onClick: () => {
              setDetailRecord(record)
              setDetailModalOpen(true)
            },
            style: { cursor: 'pointer' },
          })}
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
          scroll={{ x: 1400 }}
        />
      </Card>

      {/* Generate-all result modal */}
      <Modal
        title="批量生成结果"
        open={genAllModalOpen}
        onCancel={() => setGenAllModalOpen(false)}
        footer={null}
        width={600}
      >
        {genAllResult && (
          <div>
            <Typography.Paragraph>{genAllResult.message}</Typography.Paragraph>
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={8}>
                <Statistic
                  title="处理用户数"
                  value={genAllResult.results.length + genAllResult.errors.length}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="成功"
                  value={genAllResult.results.length}
                  valueStyle={{ color: '#3f8600' }}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="失败"
                  value={genAllResult.errors.length}
                  valueStyle={{ color: '#cf1322' }}
                />
              </Col>
            </Row>

            {genAllResult.results.length > 0 && (
              <Card title="成功详情" size="small" style={{ marginBottom: 16 }}>
                {genAllResult.results.map((r) => (
                  <div key={r.user_id}>
                    <Typography.Text>
                      {r.user_id}: 生成 {r.count} 条推荐
                    </Typography.Text>
                  </div>
                ))}
              </Card>
            )}

            {genAllResult.errors.length > 0 && (
              <Card title="失败详情" size="small">
                {genAllResult.errors.map((e) => (
                  <div key={e.user_id}>
                    <Typography.Text type="danger">
                      {e.user_id}: {e.error}
                    </Typography.Text>
                  </div>
                ))}
              </Card>
            )}
          </div>
        )}
      </Modal>

      {/* Detail modal */}
      <Modal
        title="推荐详情"
        open={detailModalOpen}
        onCancel={() => setDetailModalOpen(false)}
        footer={null}
        width={600}
      >
        {detailRecord && (
          <div>
            <Row gutter={[16, 16]}>
              <Col span={12}>
                <Typography.Text type="secondary">推荐ID</Typography.Text>
                <div>{detailRecord.id}</div>
              </Col>
              <Col span={12}>
                <Typography.Text type="secondary">用户ID</Typography.Text>
                <div>{detailRecord.user_id}</div>
              </Col>
              <Col span={12}>
                <Typography.Text type="secondary">推荐商品SKU</Typography.Text>
                <div>{detailRecord.recommended_sku}</div>
              </Col>
              <Col span={12}>
                <Typography.Text type="secondary">商品名称</Typography.Text>
                <div>{detailRecord.product_name || '-'}</div>
              </Col>
              <Col span={12}>
                <Typography.Text type="secondary">排名</Typography.Text>
                <div>
                  <Tag color={detailRecord.rank <= 3 ? 'gold' : 'default'}>
                    {detailRecord.rank}
                  </Tag>
                </div>
              </Col>
              <Col span={12}>
                <Typography.Text type="secondary">置信度</Typography.Text>
                <div>
                  <Progress
                    percent={Math.round(detailRecord.confidence * 100)}
                    size="small"
                    status={
                      detailRecord.confidence >= 0.7
                        ? 'success'
                        : detailRecord.confidence >= 0.4
                          ? 'normal'
                          : 'exception'
                    }
                    style={{ marginBottom: 0 }}
                  />
                </div>
              </Col>
              <Col span={12}>
                <Typography.Text type="secondary">来源</Typography.Text>
                <div>
                  <Tag>{detailRecord.source}</Tag>
                </div>
              </Col>
              <Col span={12}>
                <Typography.Text type="secondary">状态</Typography.Text>
                <div>
                  {(() => {
                    const colorMap: Record<string, string> = {
                      pending: 'default',
                      accepted: 'green',
                      rejected: 'red',
                    }
                    const labelMap: Record<string, string> = {
                      pending: '待处理',
                      accepted: '已采纳',
                      rejected: '已拒绝',
                    }
                    return (
                      <Tag color={colorMap[detailRecord.status] || 'default'}>
                        {labelMap[detailRecord.status] || detailRecord.status}
                      </Tag>
                    )
                  })()}
                </div>
              </Col>
              <Col span={24}>
                <Typography.Text type="secondary">推荐理由</Typography.Text>
                <div>{detailRecord.reason || '-'}</div>
              </Col>
              <Col span={12}>
                <Typography.Text type="secondary">生成时间</Typography.Text>
                <div>{detailRecord.generated_at || '-'}</div>
              </Col>
              <Col span={12}>
                <Typography.Text type="secondary">反馈时间</Typography.Text>
                <div>{detailRecord.feedback_at || '-'}</div>
              </Col>
            </Row>
          </div>
        )}
      </Modal>
    </div>
  )
}
