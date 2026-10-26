# -*- mode: python ; coding: utf-8 -*-

import os
import sys

# ===================== 自动获取 tcl/tk 资源路径 =====================
# 单文件模式如果不带 tcl/tk，打包出来的 exe 运行时就会闪退
datas = []
if sys.platform == 'win32':
    try:
        python_dir = os.path.dirname(sys.executable)
        tcl_dir = os.path.join(python_dir, 'tcl')
        tk_dir = os.path.join(python_dir, 'tk')
        
        if os.path.exists(tcl_dir):
            datas.append((tcl_dir, 'tcl'))
            print(f"[PyInstaller] 自动包含 TCL 资源: {tcl_dir}")
        if os.path.exists(tk_dir):
            datas.append((tk_dir, 'tk'))
            print(f"[PyInstaller] 自动包含 TK 资源: {tk_dir}")
    except Exception as e:
        print(f"[PyInstaller] 自动查找 tcl/tk 失败: {e}")

# ===================== 你的图片和库资源 =====================
image_files = [
    # 生肖图片
    ('image/rat.webp', '.'),
    ('image/ox.webp', '.'),
    ('image/tiger.webp', '.'),
    ('image/rabbit.webp', '.'),
    ('image/dragon.webp', '.'),
    ('image/snake.webp', '.'),
    ('image/horse.webp', '.'),
    ('image/goat.webp', '.'),
    ('image/monkey.webp', '.'),
    ('image/rooster.webp', '.'),
    ('image/dog.webp', '.'),
    ('image/pig.webp', '.'),
    # 星座图片
    ('image/Aries.webp', '.'),
    ('image/Taurus.webp', '.'),
    ('image/Gemini.webp', '.'),
    ('image/Cancer.webp', '.'),
    ('image/Leo.webp', '.'),
    ('image/Virgo.webp', '.'),
    ('image/Libra.webp', '.'),
    ('image/Scorpio.webp', '.'),
    ('image/Sagittarius.webp', '.'),
    ('image/Capricorn.webp', '.'),
    ('image/Aquarius.webp', '.'),
    ('image/Pisces.webp', '.'),
    # gmssl 库数据
    ('C:/Users/Lenovo/PycharmProjects/pythonProject/.venv/Lib/site-packages/gmssl', 'gmssl'),
]

# 合并资源
datas += image_files

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas, # ★ 这里使用合并后的 datas
    hiddenimports=[
        'babel.numbers', 
        'cryptography',
        'cryptography.fernet',
        'bcrypt',
        'gmssl',
        'gmssl.sm3',
        'gmssl.sm4',
        'gmssl.func',
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        'tkinter',     # ★ 补充 tkinter
        '_tkinter',    # ★ 补充 _tkinter
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 只排除确定不需要的大型库
        'numpy', 'pandas', 'matplotlib', 'scipy', 'seaborn', 'plotly',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'pygame', 'paramiko',
        'IPython', 'jupyter', 'notebook', 'pytest',
        'distutils', 'lib2to3', 'test', 'unittest',
        'cv2', 'moviepy',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ★ 单文件模式：把 binaries, zipfiles, datas 全塞进 EXE 里
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='小工具合集',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,  # 关闭strip避免tkinter问题
    upx=True,  
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version.txt',  # ★ 挂版本元数据，提升 exe 信誉
)