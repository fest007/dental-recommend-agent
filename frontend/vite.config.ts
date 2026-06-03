import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // 使用相对路径，确保打包后 file:// 协议能正确加载资源
  base: './',
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
    },
  },
  build: {
    // 代码分割优化
    rollupOptions: {
      output: {
        manualChunks: {
          // React 核心
          'react-core': ['react', 'react-dom'],
          'react-router': ['react-router-dom'],
          // Ant Design 按需分割
          'antd-core': ['antd'],
          'antd-icons': ['@ant-design/icons'],
          // ECharts 按需加载（只包含 graph 图表）
          'echarts': ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
        },
      },
    },
    // 启用压缩
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true,
        pure_funcs: ['console.log'],
      },
      mangle: true,
    },
    // chunk 大小警告阈值
    chunkSizeWarningLimit: 500,
  },
})
