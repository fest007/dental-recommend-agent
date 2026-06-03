# 构建说明

## 打包方式

### macOS DMG

在 Mac 上打包：

```bash
./scripts/build-dmg.sh
```

### Windows EXE

在 Windows 上打包：

```cmd
scripts\build-exe.bat
```

### 自动构建（推荐）

使用 GitHub Actions 自动构建两个平台：

```bash
./scripts/release.sh 1.0.0
```

---

## 脚本说明

| 脚本 | 平台 | 说明 |
|------|------|------|
| `scripts/build-dmg.sh` | macOS | 打包 macOS DMG |
| `scripts/build-exe.bat` | Windows | 打包 Windows EXE |
| `scripts/release.sh` | 任意 | 触发 GitHub Actions 构建 |

---

## 重要说明

### Windows 构建

**必须在 Windows 环境下构建**，不支持 macOS 交叉编译。

原因：PyInstaller 只能打包当前操作系统的可执行文件。在 macOS 上打包的后端是 macOS 格式，无法在 Windows 上运行。

### GitHub Actions

推荐使用 GitHub Actions 自动构建，它会：
- 在 macOS 上构建 DMG
- 在 Windows 上构建 EXE
- 自动创建 Release 草稿

---

## 常见问题

### Q: 打包后文件在哪？

A: `release/` 目录
- macOS: `牙科设备推荐Agent-1.0.0.dmg`
- Windows: `牙科设备推荐Agent Setup 1.0.0.exe`

### Q: macOS 提示"已损坏"怎么办？

A: 运行以下命令：
```bash
xattr -cr /Applications/牙科设备推荐Agent.app
```

---

## 完整流程

### macOS 用户

```bash
# 安装依赖
npm install
cd frontend && npm install && cd ..
cd backend && pip install -r requirements.txt && pip install pyinstaller && cd ..

# 打包 DMG
./scripts/build-dmg.sh

# 或使用 GitHub Actions 构建所有平台
./scripts/release.sh 1.0.0
```

### Windows 用户

```cmd
REM 安装依赖
npm install
cd frontend && npm install && cd ..
cd backend && pip install -r requirements.txt && pip install pyinstaller && cd ..

REM 打包 EXE
scripts\build-exe.bat
```
