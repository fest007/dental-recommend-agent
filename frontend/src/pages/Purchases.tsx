import React, { useEffect, useState, useCallback } from 'react'
import {
  Table,
  Button,
  Input,
  Modal,
  Form,
  Upload,
  Space,
  Popconfirm,
  message,
  Card,
  Typography,
  DatePicker,
  InputNumber,
} from 'antd'
import {
  PlusOutlined,
  UploadOutlined,
  SearchOutlined,
  EditOutlined,
  DeleteOutlined,
  GroupOutlined,
} from '@ant-design/icons'
import type { UploadProps } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import {
  getPurchases,
  createPurchase,
  updatePurchase,
  deletePurchase,
  importPurchases,
  type Purchase,
} from '../services/api'
import styles from './Purchases.module.less'

export default function Purchases() {
  const [data, setData] = useState<Purchase[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(false)
  const [userIdFilter, setUserIdFilter] = useState('')
  const [dateRange, setDateRange] = useState<[string | null, string | null]>([null, null])
  const [groupByUser, setGroupByUser] = useState(false)

  // Compute grouped data
  const groupedData = React.useMemo(() => {
    if (!groupByUser) return null
    const groups: Record<string, Purchase[]> = {}
    for (const item of data) {
      const uid = item.user_id || '未知'
      if (!groups[uid]) groups[uid] = []
      groups[uid].push(item)
    }
    return Object.entries(groups).map(([userId, items]) => ({
      userId,
      count: items.length,
      items,
    }))
  }, [data, groupByUser])

  // Modal state
  const [modalOpen, setModalOpen] = useState(false)
  const [editingItem, setEditingItem] = useState<Purchase | null>(null)
  const [form] = Form.useForm()

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getPurchases({
        page,
        page_size: pageSize,
        user_id: userIdFilter || undefined,
        date_from: dateRange[0] || undefined,
        date_to: dateRange[1] || undefined,
      })
      setData(res.data.items)
      setTotal(res.data.total)
    } catch {
      message.error('加载购买记录失败')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, userIdFilter, dateRange])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleSearch = (value: string) => {
    setUserIdFilter(value)
    setPage(1)
  }

  const handleAdd = () => {
    setEditingItem(null)
    form.resetFields()
    setModalOpen(true)
  }

  const handleEdit = (record: Purchase) => {
    setEditingItem(record)
    form.setFieldsValue({
      user_id: record.user_id,
      sku: record.sku,
      product_name: record.product_name,
      quantity: record.quantity,
      purchase_date: record.purchase_date ? dayjs(record.purchase_date) : undefined,
      original_sku: record.original_sku,
    })
    setModalOpen(true)
  }

  const handleDelete = async (id: number) => {
    try {
      await deletePurchase(id)
      message.success('删除成功')
      fetchData()
    } catch {
      message.error('删除失败')
    }
  }

  const handleModalOk = async () => {
    try {
      const values = await form.validateFields()
      const payload = {
        ...values,
        purchase_date: values.purchase_date
          ? values.purchase_date.format('YYYY-MM-DD')
          : undefined,
      }

      if (editingItem) {
        await updatePurchase(editingItem.id, payload)
        message.success('更新成功')
      } else {
        await createPurchase(payload)
        message.success('创建成功')
      }
      setModalOpen(false)
      fetchData()
    } catch {
      // validation or API error
    }
  }

  const uploadProps: UploadProps = {
    accept: '.xlsx,.xls',
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      try {
        const res = await importPurchases(file as File)
        const { imported, skipped, note } = res.data
        if (note) {
          message.warning(note, 5)
        } else {
          message.success(`导入成功: ${imported} 条, 跳过: ${skipped} 条`)
        }
        onSuccess?.(res.data)
        fetchData()
      } catch (err) {
        message.error('导入失败')
        onError?.(err as Error)
      }
    },
  }

  const columns: ColumnsType<Purchase> = [
    {
      title: '用户ID',
      dataIndex: 'user_id',
      key: 'user_id',
      width: 120,
    },
    {
      title: 'SKU',
      dataIndex: 'sku',
      key: 'sku',
      width: 130,
      ellipsis: true,
    },
    {
      title: '商品名称',
      dataIndex: 'product_name',
      key: 'product_name',
      ellipsis: true,
    },
    {
      title: '数量',
      dataIndex: 'quantity',
      key: 'quantity',
      width: 80,
      align: 'right',
    },
    {
      title: '购买日期',
      dataIndex: 'purchase_date',
      key: 'purchase_date',
      width: 120,
      render: (v: string) => (v ? v.substring(0, 10) : '-'),
    },
    {
      title: '原始SKU',
      dataIndex: 'original_sku',
      key: 'original_sku',
      width: 130,
      ellipsis: true,
      render: (v: string) => v || '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_, record) => (
        <Space>
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
            size="small"
          >
            编辑
          </Button>
          <Popconfirm
            title="确认删除此记录?"
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
      <Typography.Title level={3}>购买记录管理</Typography.Title>

      {/* Search and filters */}
      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input.Search
            placeholder="按用户ID筛选"
            allowClear
            enterButton={<SearchOutlined />}
            onSearch={handleSearch}
            style={{ width: 300 }}
          />
          <DatePicker.RangePicker
            placeholder={['开始日期', '结束日期']}
            onChange={(dates) => {
              if (dates) {
                setDateRange([dates[0]?.format('YYYY-MM-DD') || null, dates[1]?.format('YYYY-MM-DD') || null])
              } else {
                setDateRange([null, null])
              }
              setPage(1)
            }}
          />
          <Button
            icon={<GroupOutlined />}
            type={groupByUser ? 'primary' : 'default'}
            onClick={() => setGroupByUser(!groupByUser)}
          >
            {groupByUser ? '平铺视图' : '按用户分组'}
          </Button>
          <Upload {...uploadProps}>
            <Button icon={<UploadOutlined />}>导入Excel</Button>
          </Upload>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
            新增记录
          </Button>
        </Space>
      </Card>

      {/* Table */}
      <Card>
        {groupByUser && groupedData ? (
          // Grouped view: one expandable row per user
          <Table<{ userId: string; count: number; items: Purchase[] }>
            rowKey="userId"
            columns={[
              { title: '用户ID', dataIndex: 'userId', key: 'userId', width: 150 },
              { title: '记录数', dataIndex: 'count', key: 'count', width: 100, align: 'right' },
              { title: '最新购买', key: 'latest', render: (_, r) => r.items[0]?.purchase_date?.substring(0, 10) || '-' },
            ]}
            dataSource={groupedData}
            loading={loading}
            expandable={{
              expandedRowRender: (record) => (
                <Table<Purchase>
                  rowKey="id"
                  columns={columns.filter(c => c.key !== 'action')}
                  dataSource={record.items}
                  pagination={false}
                  size="small"
                />
              ),
            }}
            pagination={false}
          />
        ) : (
          <Table<Purchase>
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
        )}
      </Card>

      {/* Add/Edit Modal */}
      <Modal
        title={editingItem ? '编辑购买记录' : '新增购买记录'}
        open={modalOpen}
        onOk={handleModalOk}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
        width={600}
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item
            name="user_id"
            label="用户ID"
            rules={[{ required: true, message: '请输入用户ID' }]}
          >
            <Input placeholder="例如: KH3734" />
          </Form.Item>
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
          <Form.Item name="quantity" label="数量" initialValue={1}>
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="purchase_date"
            label="购买日期"
            rules={[{ required: true, message: '请选择购买日期' }]}
          >
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="original_sku" label="原始SKU">
            <Input placeholder="原始SKU (可选)" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
