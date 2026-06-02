import React, { useEffect, useState, useCallback } from 'react'
import {
  Table,
  Button,
  Tag,
  Space,
  Card,
  Descriptions,
  Modal,
  Typography,
  message,
  Progress,
  Alert,
  Spin,
} from 'antd'
import { EyeOutlined, ThunderboltOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { getEnrichedProducts, enrichProductsStream, buildProductRelations, type ProductEnriched, type EnrichProgressEvent } from '../services/api'
import styles from './EnrichedProducts.module.less'

export default function EnrichedProducts() {
  const [data, setData] = useState<ProductEnriched[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(false)

  // Enrichment
  const [enriching, setEnriching] = useState(false)
  const [enrichResult, setEnrichResult] = useState<{ total: number; enriched: number; failed: number } | null>(null)
  const [enrichProgress, setEnrichProgress] = useState<{ current: number; total: number; sku: string; concurrency?: number; already_enriched?: number } | null>(null)

  // Relation building
  const [buildingRelations, setBuildingRelations] = useState(false)
  const [relationResult, setRelationResult] = useState<{ consumable: number; accessory: number; same_category: number; complementary: number; same_series: number; co_purchased: number; total: number } | null>(null)

  // Detail modal
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailItem, setDetailItem] = useState<ProductEnriched | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getEnrichedProducts({ page, page_size: pageSize })
      setData(res.data.items)
      setTotal(res.data.total)
    } catch {
      message.error('加载增强商品列表失败')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleBuildRelations = async () => {
    setBuildingRelations(true)
    setRelationResult(null)
    try {
      const res = await buildProductRelations(true)
      const r = res.data
      setRelationResult(r)
      if (r.total > 0) {
        message.success(`关系图谱构建完成：共 ${r.total} 条关系（消耗品${r.consumable} / 配件${r.accessory} / 同品类${r.same_category} / 互补${r.complementary} / 同系列${r.same_series} / 共现${r.co_purchased}）`)
      } else {
        message.info('没有增强数据可供构建关系图谱，请先执行LLM增强')
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail || '构建关系图谱失败'
      message.error(detail)
    } finally {
      setBuildingRelations(false)
    }
  }

  const handleViewDetail = (record: ProductEnriched) => {
    setDetailItem(record)
    setDetailOpen(true)
  }

  const handleEnrich = () => {
    setEnriching(true)
    setEnrichResult(null)
    setEnrichProgress(null)

    enrichProductsStream(
      {},
      (event: EnrichProgressEvent) => {
        if (event.type === 'start') {
          setEnrichProgress({ current: 0, total: event.total || 0, sku: '', concurrency: 20, already_enriched: event.already_enriched })
          if (event.already_enriched && event.already_enriched > 0) {
            message.info(`已有 ${event.already_enriched} 条增强记录，将跳过`)
          }
        } else if (event.type === 'progress') {
          setEnrichProgress({
            current: event.current || 0,
            total: event.total || 0,
            sku: event.sku || '',
            concurrency: event.concurrency,
          })
        } else if (event.type === 'concurrency_change') {
          setEnrichProgress(prev => prev ? { ...prev, concurrency: event.concurrency } : prev)
        } else if (event.type === 'done') {
          const result = { total: event.total || 0, enriched: event.enriched || 0, failed: event.failed || 0 }
          setEnrichResult(result)
          setEnrichProgress(null)
          setEnriching(false)
          if (result.enriched > 0) {
            message.success(`增强完成：成功 ${result.enriched} 条，失败 ${result.failed} 条`)
            fetchData()
          } else if (result.total === 0) {
            message.info('没有需要增强的商品')
          }
        }
      },
      (err) => {
        message.error('商品增强失败：' + err.message)
        setEnriching(false)
        setEnrichProgress(null)
      },
    )
  }

  const columns: ColumnsType<ProductEnriched> = [
    {
      title: 'SKU',
      dataIndex: 'sku',
      key: 'sku',
      width: 130,
      ellipsis: true,
    },
    {
      title: '商品名称',
      dataIndex: 'name',
      key: 'name',
      ellipsis: true,
    },
    {
      title: '品牌',
      dataIndex: 'brand',
      key: 'brand',
      width: 120,
      ellipsis: true,
      render: (v: string) => v || '-',
    },
    {
      title: '一级品类',
      dataIndex: 'category_l1',
      key: 'category_l1',
      width: 120,
      ellipsis: true,
    },
    {
      title: '二级品类',
      dataIndex: 'category_l2',
      key: 'category_l2',
      width: 120,
      ellipsis: true,
    },
    {
      title: '产品类型',
      dataIndex: 'product_type',
      key: 'product_type',
      width: 100,
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '置信度',
      dataIndex: 'enrichment_confidence',
      key: 'enrichment_confidence',
      width: 120,
      render: (v: number) => (
        <Progress
          percent={Math.round(v * 100)}
          size="small"
          status={v >= 0.7 ? 'success' : v >= 0.4 ? 'normal' : 'exception'}
        />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, record) => (
        <Button
          type="link"
          icon={<EyeOutlined />}
          onClick={() => handleViewDetail(record)}
          size="small"
        >
          详情
        </Button>
      ),
    },
  ]

  return (
    <div className={styles.container}>
      <Typography.Title level={3}>增强商品管理</Typography.Title>

      <Card
        style={{ marginBottom: 16 }}
      >
        <Space wrap>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            onClick={handleEnrich}
            loading={enriching}
            disabled={enriching}
            size="large"
          >
            {enriching ? '增强中...' : '开始LLM增强'}
          </Button>
          <Button
            icon={<ThunderboltOutlined />}
            onClick={handleBuildRelations}
            loading={buildingRelations}
            size="large"
          >
            {buildingRelations ? '构建中...' : '构建关系图谱'}
          </Button>
          <Typography.Text type="secondary">
            1. LLM增强 → 2. 构建关系图谱 → 3. 推荐引擎可用
          </Typography.Text>
        </Space>
        {enrichProgress && (
          <div style={{ marginTop: 12 }}>
            <Space style={{ marginBottom: 4 }}>
              <Spin size="small" />
              <Typography.Text type="secondary">
                正在增强 {enrichProgress.current}/{enrichProgress.total}
                {enrichProgress.concurrency && ` (并发${enrichProgress.concurrency})`}
                {enrichProgress.sku && ` — ${enrichProgress.sku}`}
              </Typography.Text>
            </Space>
            <Progress
              percent={Math.round((enrichProgress.current / enrichProgress.total) * 100)}
              status="active"
              strokeColor={{ from: '#108ee9', to: '#87d068' }}
            />
          </div>
        )}
        {enrichResult && (
          <Alert
            style={{ marginTop: 12 }}
            type={enrichResult.failed > 0 ? 'warning' : 'success'}
            message={`增强完成：总计 ${enrichResult.total} 条，成功 ${enrichResult.enriched} 条，失败 ${enrichResult.failed} 条`}
            showIcon
          />
        )}
        {relationResult && (
          <Alert
            style={{ marginTop: 8 }}
            type="success"
            message={`关系图谱构建完成：消耗品 ${relationResult.consumable} / 配件 ${relationResult.accessory} / 同品类 ${relationResult.same_category} / 互补 ${relationResult.complementary} / 同系列 ${relationResult.same_series} / 共现 ${relationResult.co_purchased}，共 ${relationResult.total} 条`}
            showIcon
          />
        )}
      </Card>

      <Card>
        <Table<ProductEnriched>
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
          scroll={{ x: 1000 }}
        />
      </Card>

      {/* Detail Modal */}
      <Modal
        title="增强商品详情"
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        width={700}
      >
        {detailItem && (
          <Descriptions column={2} bordered size="small">
            <Descriptions.Item label="ID">{detailItem.id}</Descriptions.Item>
            <Descriptions.Item label="关联商品ID">{detailItem.product_id}</Descriptions.Item>
            <Descriptions.Item label="SKU">{detailItem.sku}</Descriptions.Item>
            <Descriptions.Item label="商品名称">{detailItem.name}</Descriptions.Item>
            <Descriptions.Item label="品牌">{detailItem.brand || '-'}</Descriptions.Item>
            <Descriptions.Item label="产品类型">{detailItem.product_type}</Descriptions.Item>
            <Descriptions.Item label="一级品类">{detailItem.category_l1}</Descriptions.Item>
            <Descriptions.Item label="二级品类">{detailItem.category_l2}</Descriptions.Item>
            <Descriptions.Item label="使用场景" span={2}>
              {detailItem.usage_scenario || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="关键词" span={2}>
              {(detailItem.keywords || []).length > 0
                ? detailItem.keywords.map((k) => <Tag key={k}>{k}</Tag>)
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="消耗品" span={2}>
              {(detailItem.consumables || []).length > 0
                ? detailItem.consumables.map((c: any, i: number) => {
                    const label = typeof c === 'string' ? c : `${c.name || ''}${c.relation ? `(${c.relation})` : ''}`
                    return <Tag color="orange" key={i}>{label}</Tag>
                  })
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="相关配件" span={2}>
              {(detailItem.related_accessories || []).length > 0
                ? detailItem.related_accessories.map((a: any, i: number) => {
                    const label = typeof a === 'string' ? a : `${a.name || ''}${a.relation ? `(${a.relation})` : ''}`
                    return <Tag color="cyan" key={i}>{label}</Tag>
                  })
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="采购周期(天)">
              {detailItem.typical_purchase_cycle_days ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="单位提示">{detailItem.unit_hint}</Descriptions.Item>
            <Descriptions.Item label="置信度">
              <Progress
                percent={Math.round(detailItem.enrichment_confidence * 100)}
                size="small"
                status={detailItem.enrichment_confidence >= 0.7 ? 'success' : 'normal'}
              />
            </Descriptions.Item>
            <Descriptions.Item label="LLM模型">{detailItem.llm_model}</Descriptions.Item>
            <Descriptions.Item label="向量ID">{detailItem.embedding_vector_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="增强时间">
              {detailItem.enriched_at ? new Date(detailItem.enriched_at).toLocaleString('zh-CN') : '-'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  )
}
