# 发布说明

## 自动发布到 GitHub Release

### 流程

```
1. 修改代码并提交
2. 创建 tag (v1.0.0)
3. 推送到 GitHub
4. GitHub Actions 自动构建:
   - macOS 上构建 DMG
   - Windows 上构建 EXE
5. 自动创建 Release 草稿
6. 手动确认发布
```

### 操作步骤

#### 1. 修改版本号

编辑 `package.json`:
```json
{
  "version": "1.0.1"
}
```

#### 2. 提交代码

```bash
git add .
git commit -m "feat: 版本 1.0.1"
git push origin main
```

#### 3. 创建 Tag 并推送

```bash
git tag v1.0.1
git push origin v1.0.1
```

或者使用快捷脚本：
```bash
./scripts/release.sh 1.0.1
```

#### 4. 等待构建

在 GitHub 仓库的 Actions 页面查看构建进度：
- https://github.com/你的用户名/仓库名/actions

#### 5. 发布 Release

构建完成后会自动创建 Release 草稿：
1. 进入 GitHub 仓库的 Releases 页面
2. 找到草稿版本
3. 编辑更新说明
4. 点击 "Publish release"

---

## 手动发布

如果不使用 GitHub Actions，可以手动上传：

```bash
# 1. 本地构建
./build-dmg.sh      # macOS
./build-exe.bat     # Windows (在 Windows 上运行)

# 2. 打包文件
zip -r release-v1.0.1.zip release/*

# 3. 上传到 GitHub Release
# 进入 GitHub -> Releases -> Draft a new release
# 上传 release-v1.0.1.zip
```

---

## Release 文件说明

每次发布应包含：

| 文件 | 平台 | 说明 |
|------|------|------|
| `牙科设备推荐Agent-1.0.0.dmg` | macOS | Mac 安装包 |
| `牙科设备推荐Agent Setup 1.0.0.exe` | Windows | Windows 安装程序 |

---

## 版本号规范

采用语义化版本：`主版本.次版本.修订号`

- **主版本**: 不兼容的 API 修改
- **次版本**: 向下兼容的功能性新增
- **修订号**: 向下兼容的问题修正

示例：
- `1.0.0` -> `1.0.1`: 修复 bug
- `1.0.0` -> `1.1.0`: 新增功能
- `1.0.0` -> `2.0.0`: 重大更新

---

## 注意事项

1. **首次发布**: 需要在 GitHub 仓库设置中添加 Actions 权限
2. **Tag 格式**: 必须以 `v` 开头，如 `v1.0.0`
3. **构建时间**: 约 10-20 分钟
4. **Release 草稿**: 自动创建为草稿，需手动确认发布
