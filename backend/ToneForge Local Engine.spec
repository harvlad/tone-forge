# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('/Users/mattharvey/Sites/tone-forge/backend/tone_forge', 'tone_forge'), ('/Users/mattharvey/Sites/tone-forge/backend/data', 'data'), ('/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/demucs/remote', 'demucs/remote'), ('/Library/Frameworks/Python.framework/Versions/3.10/lib/python3.10/site-packages/basic_pitch', 'basic_pitch')]
datas += collect_data_files('demucs')
datas += collect_data_files('basic_pitch')


a = Analysis(
    ['/Users/mattharvey/Sites/tone-forge/backend/local_engine/tray.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['uvicorn', 'uvicorn.logging', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'fastapi', 'starlette', 'pydantic', 'pystray', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'librosa', 'soundfile', 'audioread', 'resampy', 'torch', 'torchaudio', 'demucs', 'demucs.pretrained', 'demucs.apply', 'basic_pitch', 'numpy', 'scipy', 'scipy.signal', 'scipy.fft', 'tone_forge', 'tone_forge.analyzer', 'tone_forge.stem_separator', 'tone_forge.midi_extractor', 'tone_forge.auto_detect', 'local_engine.server'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ToneForge Local Engine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ToneForge Local Engine',
)
app = BUNDLE(
    coll,
    name='ToneForge Local Engine.app',
    icon=None,
    bundle_identifier='com.toneforge.localengine',
)
