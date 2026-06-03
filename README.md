# 牙科设备推荐Agent

基于 LangGraph 的 B2B 牙科设备智能推荐系统，支持用户画像分析、商品关系图谱、智能对话推荐等功能。

> 📌 本项目采用 [CC-BY-NC-SA 4.0](LICENSE) 许可协议，仅供学习和非商业使用。

## ✨ 功能特性

### 🤖 智能对话 Agent
- ReAct 架构，支持工具调用和多轮对话
- 流式响应，实时显示思考过程
- 支持查询用户画像、购买记录、商品信息
- 支持添加/删除购买记录、生成推荐等操作

### 📊 用户画像管理
- 自动分析用户购买行为
- 品类偏好、品牌偏好分析
- 采购周期预测
- 耗材补货提醒

### 🔗 商品关系图谱
- 可视化商品关系网络
- 支持多种关系类型：消耗品、配件、同系列、互补等
- 交互式图谱浏览

### 🎯 智能推荐
- 多路召回：图召回、向量召回、规则召回、知识图谱召回
- LLM 排序，生成个性化推荐理由
- 支持反馈优化

### 📦 商品管理
- 商品信息录入和管理
- LLM 增强商品数据
- Excel 导入导出

## 🚀 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- OpenAI API Key（或兼容的第三方服务）

### 安装

```bash
# 克隆项目
git clone https://github.com/your-username/dental-recommend-agent.git
cd dental-recommend-agent

# 安装后端依赖
cd backend
pip install -r requirements.txt
cd ..

# 安装前端依赖
cd frontend
npm install
cd ..

# 安装根目录依赖（用于 Electron）
npm install
```

### 配置

```bash
# 复制配置文件
cp backend/config.yaml.example backend/config.yaml

# 编辑配置，填入 API Key
# macOS: vim backend/config.yaml
# Windows: notepad backend\config.yaml
```

### 运行

```bash
# 开发模式（同时启动前后端）
npm run dev

# 或者分别启动
npm run dev:backend   # 后端 http://localhost:8765
npm run dev:frontend  # 前端 http://localhost:5173
```

访问 http://localhost:5173

### 首次使用

1. 打开「系统设置」页面，配置 LLM 的 Base URL 和 API Key
2. 点击「获取模型列表」选择模型
3. 保存配置
4. 上传产品 SKU Excel 和客户购买记录 Excel
5. 在 Agent 对话页面测试

## 📦 打包发布

### macOS DMG（在 Mac 上打包）

```bash
./scripts/build-dmg.sh
```

### Windows EXE（在 Windows 上打包）

```cmd
scripts\build-exe.bat
```

### 自动构建（推荐）

```bash
# 触发 GitHub Actions 自动构建 macOS + Windows
./scripts/release.sh 1.0.0
```

> ⚠️ Windows 安装包必须在 Windows 环境构建，不支持 macOS 交叉编译。

详细说明见 [BUILD.md](BUILD.md)

### 发布到 GitHub Release

```bash
# 自动发布（需要先提交代码）
./scripts/release.sh 1.0.0
```

详细说明见 [RELEASE.md](RELEASE.md)

## 📁 项目结构

```
dental-recommend-agent/
├── backend/                    # Python 后端
│   ├── main.py                 # 入口文件
│   ├── config.yaml.example     # 配置示例
│   ├── requirements.txt        # Python 依赖
│   ├── db/                     # 数据库模型
│   ├── routers/                # API 路由
│   ├── services/               # 业务逻辑
│   │   ├── chat_graph.py       # Agent 对话
│   │   ├── recommendation.py   # 推荐服务
│   │   └── user_profile.py     # 用户画像
│   └── utils/                  # 工具函数
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── pages/              # 页面组件
│   │   ├── services/           # API 服务
│   │   └── components/         # 公共组件
│   └── package.json
├── electron/                   # Electron 主进程
│   └── main.js
├── .github/workflows/          # GitHub Actions
│   └── release.yml             # 自动发布配置
├── build-dmg.sh                # 打包 macOS
├── build-exe.sh                # 打包 Windows (Mac)
├── scripts/build-exe.bat               # 打包 Windows
├── build-all.sh                # 打包所有平台
├── release.sh                  # 发布脚本
└── package.json
```

## 🛠️ 技术栈

### 后端
- **框架**: FastAPI
- **数据库**: SQLite + SQLAlchemy
- **Agent**: LangGraph + LangChain
- **LLM**: OpenAI API（支持第三方兼容服务）
- **向量数据库**: Qdrant

### 前端
- **框架**: React 18 + TypeScript
- **UI**: Ant Design 5
- **图表**: ECharts
- **构建**: Vite

### 桌面应用
- **框架**: Electron
- **打包**: electron-builder + PyInstaller

## 📝 API 文档

启动后端后访问：
- Swagger UI: http://localhost:8765/docs
- ReDoc: http://localhost:8765/redoc

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 📄 许可证

本项目采用 [CC-BY-NC-SA 4.0](LICENSE) 许可协议。

**您可以：**
- ✅ 共享 - 复制和分发本作品
- ✅ 演绎 - 修改和基于本作品创作

**但必须遵守：**
- 📝 署名 - 给出适当的署名
- 🚫 非商业 - 不得用于商业目的
- 🔄 相同方式共享 - 衍生作品必须采用相同许可协议

详细说明见 [LICENSE](LICENSE)

## 🙏 致谢

- [LangGraph](https://github.com/langchain-ai/langgraph) - Agent 框架
- [FastAPI](https://fastapi.tiangolo.com/) - Web 框架
- [Ant Design](https://ant.design/) - UI 组件库
- [ECharts](https://echarts.apache.org/) - 图表库

---

⭐ 如果这个项目对您有帮助，请给个 Star 支持一下！
