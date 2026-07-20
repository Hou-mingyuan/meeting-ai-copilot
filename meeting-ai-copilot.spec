# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


hiddenimports = collect_submodules("volcengine_audio")
datas = [
    ("VERSION", "."),
    ("README.md", "."),
    ("USAGE.md", "."),
    ("SECURITY.md", "."),
    ("config.example.json", "."),
    ("config.mock-offline.json", "."),
    ("tests/fixtures/meeting_question.wav", "tests/fixtures"),
    ("tests/fixtures/meeting_question.json", "tests/fixtures"),
] + collect_data_files("soundcard")

a = Analysis(
    ["src/cloud_asr_volcengine.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeetingAICopilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="MeetingAICopilot",
)
