# 构建说明

## 打包方式

### macOS DMG

在 Mac 上直接打包：

```bash
./build-dmg.sh
```

### Windows EXE

**在 Windows 上打包**（推荐）：

```cmd
build-exe.bat
```

**在 Mac 上交叉编译**（需要 Wine）：

```bash
# 先安装 Wine
brew install --cask wine-stable

# 打包
./build-exe.sh
```

### 同时打包

```bash
./build-all.sh
```

---

## 脚本说明

| 脚本 | 平台 | 说明 |
|------|------|------|
| `build-dmg.sh` | macOS | 打包 macOS DMG |
| `build-exe.sh` | macOS | 打包 Windows EXE（需要 Wine） |
| `build-exe.bat` | Windows | 打包 Windows EXE |
| `build-all.sh` | macOS | 打包所有平台 |

---

## 常见问题

### Q: 为什么 Mac 上打包的 EXE 不能用？

A: PyInstaller 只能打包当前系统的可执行文件。在 Mac 上打包的后端是 macOS 格式，Windows 无法运行。

**解决方案**：在 Windows 上运行 `build-exe.bat`

### Q: Mac 上能打包 Windows EXE 吗？

A: 可以，但需要：
1. 安装 Wine：`brew install --cask wine-stable`
2. 运行 `./build-exe.sh`

注意：这种方式打包的后端可能需要在 Windows 上重新构建。

### Q: 打包后文件在哪？

A: `release/` 目录
- macOS: `牙科设备推荐Agent-1.0.0.dmg`
- Windows: `牙科设备推荐Agent Setup 1.0.0.exe`

---

## 完整流程

### macOS 用户

```bash
# 安装依赖
npm install
cd frontend && npm install && cd ..
cd backend && pip install -r requirements.txt && pip install pyinstaller && cd ..

# 打包 DMG
./build-dmg.sh

# 如果也要打包 EXE
brew install --cask wine-stable
./build-exe.sh
```

### Windows 用户

```cmd
REM 安装依赖
npm install
cd frontend && npm install && cd ..
cd backend && pip install -r requirements.txt && pip install pyinstaller && cd ..

REM 打包 EXE
build-exe.bat
```
