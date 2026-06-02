import React, { useEffect, useState, useCallback } from 'react'
import {
  Table,
  Button,
  Input,
  Select,
  Modal,
  Form,
  Upload,
  Tag,
  Space,
  Popconfirm,
  message,
  Card,
  Descriptions,
  Typography,
} from 'antd'
import {
  PlusOutlined,
  UploadOutlined,
  SearchOutlined,
  EditOutlined,
  DeleteOutlined,
  EyeOutlined,
  DownloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import type { UploadProps } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  getProducts,
  createProduct,
  updateProduct,
  deleteProduct,
  importProducts,
  exportProducts,
  batchUpdateProductStatus,
  type ProductRaw,
} from '../services/api'
import styles from './Products.module.less'

const STATUS_OPTIONS = [
  { label: 'A1在售', value: 'A1在售' },
  { label: 'A3N在售', value: 'A3N在售' },
  { label: 'A3在售', value: 'A3在售' },
  { label: 'G1停售', value: 'G1停售' },
  { label: 'C1清仓', value: 'C1清仓' },
  { label: 'C2清仓', value: 'C2清仓' },
  { label: '说明SKU', value: '说明SKU' },
  { label: 'D等待新品到货', value: 'D等待新品到货' },
  { label: 'G3停售', value: 'G3停售' },
  { label: 'active', value: 'active' },
  { label: 'deleted', value: 'deleted' },
]

export default function Products() {
  const [data, setData] = useState<ProductRaw[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)

  // Modal state
  const [modalOpen, setModalOpen] = useState(false)
  const [editingItem, setEditingItem] = useState<ProductRaw | null>(null)
  const [form] = Form.useForm()

  // Detail drawer
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailItem, setDetailItem] = useState<ProductRaw | null>(null)

  // Batch selection
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [batchUpdating, setBatchUpdating] = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getProducts({
        page,
        page_size: pageSize,
        search: search || undefined,
        status: statusFilter,
      })
      setData(res.data.items)
      setTotal(res.data.total)
    } catch {
      message.error('加载商品列表失败')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, search, statusFilter])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleSearch = (value: string) => {
    setSearch(value)
    setPage(1)
  }

  const handleStatusFilter = (value: string | undefined) => {
    setStatusFilter(value)
    setPage(1)
  }

  const handleAdd = () => {
    setEditingItem(null)
    form.resetFields()
    setModalOpen(true)
  }

  const handleEdit = (record: ProductRaw) => {
    setEditingItem(record)
    form.setFieldsValue({
      sku: record.sku,
      product_name: record.product_name,
      old_sku: record.old_sku,
      status: record.status,
      row_num: record.row_num,
    })
    setModalOpen(true)
  }

  const handleViewDetail = (record: ProductRaw) => {
    setDetailItem(record)
    setDetailOpen(true)
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteProduct(id)
      message.success('删除成功')
      fetchData()
    } catch {
      message.error('删除失败')
    }
  }

  const handleModalOk = async () => {
    try {
      const values = await form.validateFields()
      if (editingItem) {
        await updateProduct(editingItem.id, values)
        message.success('更新成功')
      } else {
        await createProduct(values)
        message.success('创建成功')
      }
      setModalOpen(false)
      fetchData()
    } catch {
      // validation error or API error
    }
  }

  const handleExport = async () => {
    try {
      const res = await exportProducts(statusFilter)
      const url = window.URL.createObjectURL(new Blob([res.data]))
      const link = document.createElement('a')
      link.href = url
      link.download = 'products_export.xlsx'
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch {
      message.error('导出失败')
    }
  }

  const handleBatchStatus = async (status: string) => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择商品')
      return
    }
    setBatchUpdating(true)
    try {
      await batchUpdateProductStatus(selectedRowKeys as number[], status)
      message.success(`已批量更新 ${selectedRowKeys.length} 个商品状态为 ${status}`)
      setSelectedRowKeys([])
      fetchData()
    } catch {
      message.error('批量更新失败')
    } finally {
      setBatchUpdating(false)
    }
  }

  const uploadProps: UploadProps = {
    accept: '.xlsx,.xls',
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      try {
        const res = await importProducts(file as File)
        const { imported, skipped, conflicts } = res.data
        let msg = `导入成功: ${imported} 条, 跳过: ${skipped} 条`
        if (conflicts && conflicts.length > 0) {
          const conflictSummary = conflicts.slice(0, 5).map(c =>
            `${c.old_sku}→${c.new_sku}/${c.existing_sku}`
          ).join('; ')
          msg += ` | ${conflicts.length} 个旧SKU冲突: ${conflictSummary}${conflicts.length > 5 ? '...' : ''}`
          message.warning(msg, 8)
        } else {
          message.success(msg)
        }
        onSuccess?.(res.data)
        fetchData()
      } catch (err) {
        message.error('导入失败')
        onError?.(err as Error)
      }
    },
  }

  const columns: ColumnsType<ProductRaw> = [
    {
      title: 'SKU',
      dataIndex: 'sku',
      key: 'sku',
      width: 150,
      ellipsis: true,
    },
    {
      title: '商品名称',
      dataIndex: 'product_name',
      key: 'product_name',
      ellipsis: true,
    },
    {
      title: '旧SKU',
      dataIndex: 'old_sku',
      key: 'old_sku',
      width: 150,
      ellipsis: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (status: string) => {
        let color = 'default'
        if (status.includes('在售')) color = 'green'
        else if (status.includes('停售')) color = 'red'
        else if (status.includes('清仓')) color = 'orange'
        else if (status === 'active') color = 'green'
        else if (status === 'deleted') color = 'red'
        return <Tag color={color}>{status}</Tag>
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (v: string) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
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
            详情
          </Button>
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
            size="small"
          >
            编辑
          </Button>
          <Popconfirm
            title="确认删除此商品?"
            onConfirm={() => handleDelete(record.id)}
            okText="确认"
            cancelText="取消"
          >
            <Button type="link" danger icon={<DeleteOutlined />} size="small">
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className={styles.container}>
      <Typography.Title level={3}>原始商品管理</Typography.Title>

      {/* Search and filters */}
      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input.Search
            placeholder="搜索SKU或商品名称"
            allowClear
            enterButton={<SearchOutlined />}
            onSearch={handleSearch}
            style={{ width: 300 }}
          />
          <Select
            placeholder="按状态筛选"
            allowClear
            options={STATUS_OPTIONS}
            onChange={handleStatusFilter}
            style={{ width: 180 }}
          />
          <Upload {...uploadProps}>
            <Button icon={<UploadOutlined />}>导入Excel</Button>
          </Upload>
          <Button icon={<DownloadOutlined />} onClick={handleExport}>
            导出Excel
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
            新增商品
          </Button>
        </Space>
        {selectedRowKeys.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <Space>
              <span>已选 {selectedRowKeys.length} 项</span>
              <Popconfirm
                title={`确定批量停售 ${selectedRowKeys.length} 个商品？`}
                onConfirm={() => handleBatchStatus('G1停售')}
              >
                <Button size="small" icon={<StopOutlined />} loading={batchUpdating}>
                  批量停售
                </Button>
              </Popconfirm>
              <Popconfirm
                title={`确定批量上架 ${selectedRowKeys.length} 个商品？`}
                onConfirm={() => handleBatchStatus('A1在售')}
              >
                <Button size="small" type="primary" loading={batchUpdating}>
                  批量上架
                </Button>
              </Popconfirm>
              <Button size="small" onClick={() => setSelectedRowKeys([])}>
                取消选择
              </Button>
            </Space>
          </div>
        )}
      </Card>

      {/* Table */}
      <Card>
        <Table<ProductRaw>
          rowKey="id"
          columns={columns}
          dataSource={data}
          loading={loading}
          virtual
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys,
          }}
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
          scroll={{ x: 900 }}
        />
      </Card>

      {/* Add/Edit Modal */}
      <Modal
        title={editingItem ? '编辑商品' : '新增商品'}
        open={modalOpen}
        onOk={handleModalOk}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
        width={600}
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="sku" label="SKU" rules={[{ required: true, message: '请输入SKU' }]}>
            <Input placeholder="请输入SKU" />
          </Form.Item>
          <Form.Item
            name="product_name"
            label="商品名称"
            rules={[{ required: true, message: '请输入商品名称' }]}
          >
            <Input placeholder="请输入商品名称" />
          </Form.Item>
          <Form.Item name="old_sku" label="旧SKU">
            <Input placeholder="请输入旧SKU (可选)" />
          </Form.Item>
          <Form.Item name="status" label="状态" initialValue="active">
            <Select options={STATUS_OPTIONS} />
          </Form.Item>
          <Form.Item name="row_num" label="行号" initialValue={0}>
            <Input type="number" placeholder="行号" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Detail Drawer */}
      <Modal
        title="商品详情"
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        width={600}
      >
        {detailItem && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="ID">{detailItem.id}</Descriptions.Item>
            <Descriptions.Item label="SKU">{detailItem.sku}</Descriptions.Item>
            <Descriptions.Item label="商品名称">{detailItem.product_name}</Descriptions.Item>
            <Descriptions.Item label="旧SKU">{detailItem.old_sku || '-'}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={detailItem.status.includes('在售') ? 'green' : 'default'}>
                {detailItem.status}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="行号">{detailItem.row_num}</Descriptions.Item>
            <Descriptions.Item label="创建时间">
              {detailItem.created_at ? new Date(detailItem.created_at).toLocaleString('zh-CN') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="更新时间">
              {detailItem.updated_at ? new Date(detailItem.updated_at).toLocaleString('zh-CN') : '-'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  )
}
