import React, { useEffect, useState, useCallback, useRef } from 'react'
import {
  Card,
  Typography,
  message,
  Space,
  Select,
  Spin,
  Empty,
  Tag,
  Button,
  Tooltip,
  Slider,
  Row,
  Col,
  Statistic,
} from 'antd'
import {
  ReloadOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
  ExpandOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import {
  getProductGraph,
  getRelationTypes,
  type GraphData,
  type RelationType,
} from '../services/api'
import styles from './ProductGraph.module.less'

// 关系类型颜色映射
const RELATION_COLORS: Record<string, string> = {
  consumable_of: '#ff6b6b',
  accessory_of: '#ffa94d',
  same_category: '#69db7c',
  complementary: '#4dabf7',
  same_series: '#9775fa',
  upgrade_to: '#f783ac',
  consumes_with: '#ffd43b',
  co_purchased: '#868e96',
}

// 关系类型中文标签
const RELATION_LABELS: Record<string, string> = {
  consumable_of: '消耗品',
  accessory_of: '配件',
  same_category: '同品类',
  complementary: '互补',
  same_series: '同系列',
  upgrade_to: '升级',
  consumes_with: '配套使用',
  co_purchased: '共现采购',
}

export default function ProductGraph() {
  const [loading, setLoading] = useState(false)
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [relationTypes, setRelationTypes] = useState<RelationType[]>([])
  const [selectedType, setSelectedType] = useState<string | undefined>(undefined)
  const [limit, setLimit] = useState(200)
  const [chartInstance, setChartInstance] = useState<any>(null)

  const fetchRelationTypes = useCallback(async () => {
    try {
      const res = await getRelationTypes()
      setRelationTypes(res.data.types)
    } catch {
      message.error('加载关系类型失败')
    }
  }, [])

  const fetchGraphData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getProductGraph({
        relation_type: selectedType,
        limit,
      })
      setGraphData(res.data)
    } catch {
      message.error('加载图谱数据失败')
    } finally {
      setLoading(false)
    }
  }, [selectedType, limit])

  useEffect(() => {
    fetchRelationTypes()
  }, [fetchRelationTypes])

  useEffect(() => {
    fetchGraphData()
  }, [fetchGraphData])

  const getChartOption = () => {
    if (!graphData || graphData.nodes.length === 0) {
      return {}
    }

    // 计算节点大小基于连接数
    const nodeConnections: Record<string, number> = {}
    graphData.links.forEach((link) => {
      nodeConnections[link.source] = (nodeConnections[link.source] || 0) + 1
      nodeConnections[link.target] = (nodeConnections[link.target] || 0) + 1
    })

    // 构建分类列表
    const categories = graphData.categories.map((cat) => ({
      name: cat,
    }))

    // 构建节点数据
    const nodes = graphData.nodes.map((node) => ({
      id: node.id,
      name: node.name,
      value: nodeConnections[node.id] || 1,
      category: graphData.categories.indexOf(node.category),
      symbolSize: Math.min(Math.max(10, (nodeConnections[node.id] || 1) * 3), 50),
      label: {
        show: (nodeConnections[node.id] || 0) > 2,
        position: 'right',
        fontSize: 10,
      },
    }))

    // 构建连线数据
    const links = graphData.links.map((link) => ({
      source: link.source,
      target: link.target,
      value: link.weight,
      lineStyle: {
        color: RELATION_COLORS[link.relation_type] || '#ccc',
        width: Math.max(1, link.weight * 3),
        curveness: 0.3,
      },
      tooltip: {
        formatter: () => {
          const typeLabel = RELATION_LABELS[link.relation_type] || link.relation_type
          return `<strong>${typeLabel}</strong><br/>${link.description}<br/>权重: ${link.weight.toFixed(2)}`
        },
      },
    }))

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          if (params.dataType === 'node') {
            const connCount = nodeConnections[params.data.id] || 0
            return `<strong>${params.data.name}</strong><br/>SKU: ${params.data.id}<br/>品类: ${graphData.categories[params.data.category] || '-'}<br/>关联数: ${connCount}`
          }
          return ''
        },
      },
      legend: {
        data: categories.map((c) => c.name),
        type: 'scroll',
        orient: 'vertical',
        right: 10,
        top: 60,
        bottom: 20,
        textStyle: {
          fontSize: 11,
        },
      },
      animationDuration: 1500,
      animationEasingUpdate: 'quinticInOut',
      series: [
        {
          name: '商品关系图谱',
          type: 'graph',
          layout: 'force',
          data: nodes,
          links: links,
          categories: categories,
          roam: true,
          draggable: true,
          force: {
            repulsion: 200,
            gravity: 0.1,
            edgeLength: 100,
            layoutAnimation: true,
          },
          emphasis: {
            focus: 'adjacency',
            lineStyle: {
              width: 4,
            },
          },
          scaleLimit: {
            min: 0.4,
            max: 3,
          },
          lineStyle: {
            opacity: 0.6,
          },
        },
      ],
    }
  }

  const handleZoomIn = () => {
    if (chartInstance) {
      chartInstance.dispatchAction({
        type: 'graphRoam',
        zoom: 1.2,
      })
    }
  }

  const handleZoomOut = () => {
    if (chartInstance) {
      chartInstance.dispatchAction({
        type: 'graphRoam',
        zoom: 0.8,
      })
    }
  }

  const handleReset = () => {
    if (chartInstance) {
      chartInstance.dispatchAction({
        type: 'restore',
      })
    }
  }

  return (
    <div className={styles.container}>
      <Typography.Title level={3}>商品关系图谱</Typography.Title>

      {/* 控制面板 */}
      <Card style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <Space wrap>
              <span>关系类型：</span>
              <Select
                placeholder="全部类型"
                allowClear
                style={{ width: 160 }}
                value={selectedType}
                onChange={setSelectedType}
                options={[
                  { value: undefined, label: '全部类型' },
                  ...relationTypes.map((t) => ({
                    value: t.value,
                    label: `${t.label} (${t.count})`,
                  })),
                ]}
              />
              <span>数量限制：</span>
              <Slider
                min={50}
                max={1000}
                step={50}
                value={limit}
                onChange={setLimit}
                style={{ width: 200 }}
                tooltip={{ formatter: (v) => `${v} 条` }}
              />
              <Button
                icon={<ReloadOutlined />}
                onClick={fetchGraphData}
                loading={loading}
              >
                刷新
              </Button>
            </Space>
          </Col>
          <Col>
            <Space>
              <Tooltip title="放大">
                <Button icon={<ZoomInOutlined />} onClick={handleZoomIn} />
              </Tooltip>
              <Tooltip title="缩小">
                <Button icon={<ZoomOutOutlined />} onClick={handleZoomOut} />
              </Tooltip>
              <Tooltip title="重置视图">
                <Button icon={<ExpandOutlined />} onClick={handleReset} />
              </Tooltip>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* 统计信息 */}
      {graphData && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space size="large">
            <Statistic title="节点数（商品）" value={graphData.nodes.length} />
            <Statistic title="连线数（关系）" value={graphData.links.length} />
            <Statistic title="品类数" value={graphData.categories.length} />
          </Space>
        </Card>
      )}

      {/* 图例说明 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Typography.Text type="secondary">关系类型：</Typography.Text>
          {Object.entries(RELATION_LABELS).map(([key, label]) => (
            <Tag key={key} color={RELATION_COLORS[key]}>
              {label}
            </Tag>
          ))}
        </Space>
      </Card>

      {/* 图谱主体 */}
      <Card>
        <Spin spinning={loading}>
          {graphData && graphData.nodes.length > 0 ? (
            <ReactECharts
              option={getChartOption()}
              style={{ height: 'clamp(520px, calc(100dvh - 360px), 820px)' }}
              onChartReady={(instance) => setChartInstance(instance)}
              opts={{ renderer: 'canvas' }}
            />
          ) : (
            <Empty
              description={loading ? '加载中...' : '暂无图谱数据，请先构建商品关系'}
              style={{ padding: '100px 0' }}
            />
          )}
        </Spin>
      </Card>

      {/* 使用说明 */}
      <Card size="small" style={{ marginTop: 16 }}>
        <Space>
          <InfoCircleOutlined />
          <Typography.Text type="secondary">
            提示：可拖拽节点调整位置，滚轮缩放，点击节点高亮关联关系，使用右上角按钮控制视图
          </Typography.Text>
        </Space>
      </Card>
    </div>
  )
}
