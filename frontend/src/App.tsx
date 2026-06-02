import React, { useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'
import { Layout, Menu, Space, Typography } from 'antd'
import {
  MessageOutlined, ShoppingOutlined, UnorderedListOutlined,
  UserOutlined, StarOutlined, SettingOutlined, AppstoreOutlined,
  ClusterOutlined,
} from '@ant-design/icons'
import type { MenuProps } from 'antd'

import AgentChat from './pages/AgentChat'
import Products from './pages/Products'
import EnrichedProducts from './pages/EnrichedProducts'
import Purchases from './pages/Purchases'
import Profiles from './pages/Profiles'
import Recommendations from './pages/Recommendations'
import ProductGraph from './pages/ProductGraph'
import Settings from './pages/Settings'
import FloatingAssistant from './components/FloatingAssistant'
import styles from './App.module.less'

const { Sider, Content, Header } = Layout
const { Text } = Typography

type MenuItem = Required<MenuProps>['items'][number]

const menuItems: MenuItem[] = [
  { key: '/chat', icon: <MessageOutlined />, label: 'Agent对话' },
  {
    key: '/products', icon: <ShoppingOutlined />, label: '商品管理',
    children: [
      { key: '/products', label: '原始商品' },
      { key: '/products/enriched', label: '增强商品' },
      { key: '/products/graph', label: '关系图谱' },
    ],
  },
  { key: '/purchases', icon: <UnorderedListOutlined />, label: '购买记录' },
  { key: '/profiles', icon: <UserOutlined />, label: '用户画像' },
  { key: '/recommendations', icon: <StarOutlined />, label: '推荐结果' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
]

const routeMeta: Record<string, { title: string; subtitle: string }> = {
  '/chat': { title: 'Agent 工作台', subtitle: '对话、查询、执行与确认操作都集中在这里完成。' },
  '/products': { title: '原始商品', subtitle: '维护主数据、SKU 映射和销售状态。' },
  '/products/enriched': { title: '增强商品', subtitle: '查看 LLM 结构化增强、向量和关系构建结果。' },
  '/products/graph': { title: '关系图谱', subtitle: '从商品关系、共现和配套链路中观察结构。' },
  '/purchases': { title: '购买记录', subtitle: '导入、维护与排查客户真实采购明细。' },
  '/profiles': { title: '用户画像', subtitle: '核对客户价值、偏好与补货提醒。' },
  '/recommendations': { title: '推荐结果', subtitle: '生成、反馈并复核最终推荐输出。' },
  '/settings': { title: '系统设置', subtitle: '配置模型、超时、LangSmith 与连接状态。' },
}

function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const selectedKey = location.pathname
  const openKeys: string[] = selectedKey.startsWith('/products') ? ['/products'] : []
  const isChatPage = location.pathname === '/chat'
  const meta = routeMeta[location.pathname] || { title: '牙科设备推荐Agent', subtitle: '围绕商品、采购、画像和推荐的完整工作台。' }

  return (
    <Layout className={styles.layout}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        width={220}
        theme="dark"
        className={styles.sider}
      >
        <div className={`${styles.logo} ${collapsed ? styles.collapsed : ''}`}>
          <AppstoreOutlined className={styles.logoIcon} style={{ marginRight: collapsed ? 0 : 10 }} />
          {!collapsed && <span className={styles.logoText}>牙科设备推荐Agent</span>}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          defaultOpenKeys={openKeys}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout className={`${styles.mainLayout} ${collapsed ? styles.collapsed : ''}`}>
        <Header className={styles.header}>
          <Space direction="vertical" size={1} className={styles.headerCopy}>
            <Typography.Title level={4} className={styles.headerTitle}>
              牙科设备推荐Agent
            </Typography.Title>
            <Text className={styles.headerSubtitle}>
              {meta.title} · {meta.subtitle}
            </Text>
          </Space>
        </Header>
        <Content className={`${styles.content} ${isChatPage ? styles.chatContent : ''}`}>
          <Routes>
            <Route path="/" element={<Navigate to="/chat" replace />} />
            <Route path="/chat" element={<AgentChat />} />
            <Route path="/products" element={<Products />} />
            <Route path="/products/enriched" element={<EnrichedProducts />} />
            <Route path="/products/graph" element={<ProductGraph />} />
            <Route path="/purchases" element={<Purchases />} />
            <Route path="/profiles" element={<Profiles />} />
            <Route path="/recommendations" element={<Recommendations />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </Content>
        {!isChatPage && <FloatingAssistant />}
      </Layout>
    </Layout>
  )
}

function App() {
  return (
    <BrowserRouter>
      <AppLayout />
    </BrowserRouter>
  )
}

export default App
