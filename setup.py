"""Build LMemM.app (macOS):  ./build_app.sh   (alias build, uses this repo and its data/)

LSUIElement keeps it out of the Dock: LMemM lives in the menu bar and as the pill.
Permissions (Screen Recording, Accessibility, Input Monitoring, Microphone) are granted to
LMemM.app itself, not to the terminal it happens to be started from.
"""
from setuptools import setup
from pathlib import Path
import sys

STANDALONE='-A' not in sys.argv and '--alias' not in sys.argv
resources=[]
if STANDALONE:
    resources=[('', ['lmemm.py','packaging/standalone.json'])]
    for directory in ('src','semantic_memory','scripts','packaging','assets','extensions','bin'):
        grouped={}
        for path in Path(directory).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and 'app_bg_v2' not in path.parts and path.suffix not in {'.pyc','.log'}:
                grouped.setdefault(str(path.parent),[]).append(str(path))
        resources.extend(grouped.items())

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "assets/LMemM.icns",
    "plist": {
        "CFBundleName": "LMemM",
        "CFBundleDisplayName": "LMemM",
        "CFBundleIdentifier": "com.adityakhuntia.lmemm",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "LSEnvironment": {"PYTHONUTF8": "1", "LANG": "en_US.UTF-8"},   # an app has no shell locale: files would read as ASCII
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "LMemM listens when you press the note shortcut.",
        "NSSpeechRecognitionUsageDescription": "LMemM turns your spoken note into text on this Mac.",
        "NSAppleEventsUsageDescription": "LMemM reads the address of the browser tab you are on.",
    },
}
if STANDALONE:
    sys.path.insert(0,str(Path('src').resolve()))
    OPTIONS['packages']=['numpy','PIL','mcp','anyio','semantic_memory']
    OPTIONS['includes']=[p.stem for p in Path('src').glob('*.py')]+['AppKit','Foundation','Quartz','ApplicationServices','Vision','objc']
    OPTIONS['excludes']=['pytest','tkinter','matplotlib','IPython','jupyter','test','tests']

setup(app=["packaging/app_main.py"], data_files=resources, options={"py2app": OPTIONS}, setup_requires=["py2app"])
