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
        'aiosqlite',
        'sqlalchemy.dialects.sqlite',
        'multipart',
        'openai',
        'qdrant_client',
        'apscheduler',
        'apscheduler.schedulers.asyncio',
        'apscheduler.triggers.cron',
        'cryptography',
        'email',
        'email.mime',
        'email.mime.text',
        'email.mime.multipart',
        'email.mime.base',
        'email.header',
        'email.utils',
        'email.charset',
        'email.encoders',
        'email.errors',
        'email.parser',
        'email.feedparser',
        'html',
        'http',
        'http.client',
        'http.server',
        'xml',
        'xml.etree',
        'xml.etree.ElementTree',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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
        'unittest',
        'doctest',
        'test',
        'setuptools',
        'pip',
        'wheel',
        'distutils',
        'pydoc',
        'argparse',
        'calendar',
        'pyexpat',
        'pdb',
        'profile',
        'cProfile',
        'timeit',
        'trace',
        'turtle',
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
