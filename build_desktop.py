"""Build the desktop derivative without legacy browser modules or dependencies."""
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parent
cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
     '--name','AutovisorDesktop','--collect-all','rapidocr','--collect-all','onnxruntime',
     '--collect-all','sv_ttk','--collect-submodules','keyring.backends','--collect-submodules','jaraco',
     '--copy-metadata','rapidocr','--copy-metadata','keyring',
     '--exclude-module','playwright','--exclude-module','pytest','--exclude-module','torch',
     '--exclude-module','paddle','--exclude-module','openvino','--exclude-module','tensorrt',
     '--exclude-module','matplotlib','--exclude-module','scipy','--exclude-module','pandas',
     '--add-data',f'{root/"LICENSE"};.',str(root/'DesktopLauncher.py')]
subprocess.run(cmd,cwd=root,check=True)
