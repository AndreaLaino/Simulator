import json
import shlex
import subprocess
import sys
from typing import List, Optional

UI_HELPER = sys.executable.replace('python', 'python3') if sys.executable else 'python3'
HELPER_PATH = None
import os
HERE = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
POSSIBLE = os.path.join(HERE, 'scripts', 'ui_file_dialog.py')
if os.path.exists(POSSIBLE):
    HELPER_PATH = POSSIBLE
else:
    HELPER_PATH = 'scripts/ui_file_dialog.py'


def _run_helper(mode: str, title: str = '', initialdir: Optional[str] = None, filetypes: Optional[List] = None, initialfile: Optional[str] = None, defaultextension: Optional[str] = None, timeout: int = 15):
    if not HELPER_PATH:
        return None
    cmd = [sys.executable, HELPER_PATH, '--mode', mode]
    if title:
        cmd += ['--title', title]
    if initialdir:
        cmd += ['--initialdir', initialdir]
    if filetypes:
        cmd += ['--filetypes', json.dumps(filetypes)]
    if initialfile:
        cmd += ['--initialfile', initialfile]
    if defaultextension:
        cmd += ['--defaultextension', defaultextension]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, text=True)
        if proc.returncode == 0 and proc.stdout:
            return json.loads(proc.stdout)
        else:
            return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def ask_open_file(title: str = 'Open file', initialdir: Optional[str] = None, filetypes: Optional[List] = None) -> Optional[str]:
    res = _run_helper('open', title=title, initialdir=initialdir, filetypes=filetypes)
    if res and 'path' in res:
        return res['path']
    return None


def ask_open_files(title: str = 'Open files', initialdir: Optional[str] = None, filetypes: Optional[List] = None) -> Optional[List[str]]:
    res = _run_helper('open_multi', title=title, initialdir=initialdir, filetypes=filetypes)
    if res and 'paths' in res:
        return res['paths']
    return None


def ask_save_file(title: str = 'Save file', initialdir: Optional[str] = None, filetypes: Optional[List] = None, initialfile: Optional[str] = None, defaultextension: Optional[str] = None) -> Optional[str]:
    res = _run_helper('save', title=title, initialdir=initialdir, filetypes=filetypes, initialfile=initialfile, defaultextension=defaultextension)
    if res and 'path' in res:
        return res['path']
    return None


def ask_directory(title: str = 'Select folder', initialdir: Optional[str] = None) -> Optional[str]:
    res = _run_helper('dir', title=title, initialdir=initialdir)
    if res and 'path' in res:
        return res['path']
    return None
