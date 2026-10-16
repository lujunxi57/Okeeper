#!/usr/bin/env python3
"""Run unit regressions with no access to production scheduler state."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
temporary_root=Path(os.environ.get('TMPDIR',str(Path.home()/'tmp')))
temporary_root.mkdir(parents=True,exist_ok=True)
os.environ['TMPDIR']=str(temporary_root)
with tempfile.TemporaryDirectory(prefix='okeeper-unit-',dir=temporary_root) as state:
    os.environ['OKEEPER_STATE_DIR']=state
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
