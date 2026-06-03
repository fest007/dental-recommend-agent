# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for building the backend as a standalone executable.

Usage:
    cd backend
    pyinstaller backend.spec

Output: dist/backend (or backend.exe on Windows)
"""

import sys
import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('config.yaml.example', '.'),
    ],
    hiddenimports=[
        # uvicorn 核心
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # FastAPI 运行时必需
        'multipart',
        'email',
        'email.mime',
        'email.mime.text',
        'email.mime.multipart',
        'email.header',
        'email.utils',
        'email.parser',
        'email.feedparser',
        'calendar',
        # 数据库驱动
        'aiosqlite',
        'sqlalchemy',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.ext.asyncio',
        'sqlalchemy.orm',
        'sqlalchemy.orm.session',
        'sqlalchemy.orm.scoping',
        # 项目直接依赖
        'openai',
        'openai.resources',
        'openai.resources.chat',
        'openai.resources.embeddings',
        'qdrant_client',
        'qdrant_client.http',
        'qdrant_client.models',
        'apscheduler',
        'apscheduler.schedulers',
        'apscheduler.schedulers.asyncio',
        'apscheduler.triggers',
        'apscheduler.triggers.cron',
        'cryptography',
        # LangGraph / LangChain
        'langgraph',
        'langgraph.graph',
        'langgraph.checkpoint',
        'langgraph.checkpoint.sqlite',
        'langgraph.checkpoint.sqlite.aio',
        'langgraph.prebuilt',
        'langchain_core',
        'langchain_core.messages',
        'langchain_core.tools',
        'langchain_core.runnables',
        'langchain_openai',
        'langchain_openai.chat_models',
        # httpx 网络库
        'httpx',
        'httpx._transports',
        'httpx._transports.default',
        'httpcore',
        'httpcore._backends',
        'httpcore._backends.sync',
        'httpcore._backends.async_',
        # http 标准库
        'http',
        'http.client',
        'http.server',
        'http.cookies',
        # 编码相关
        'encodings',
        'encodings.utf_8',
        'encodings.ascii',
        # 其他常用标准库
        'json',
        'logging',
        'logging.config',
        'logging.handlers',
        'uuid',
        'datetime',
        'pathlib',
        'tempfile',
        'shutil',
        'copy',
        'dataclasses',
        'typing',
        'typing_extensions',
        'contextvars',
        'concurrent',
        'concurrent.futures',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 只排除确定不需要的大型库
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 桌面应用不显示控制台窗口
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='backend',
)
