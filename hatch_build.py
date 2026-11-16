"""A wheel with SWAT+ inside: set SWATPLUS_BMI_BUNDLE to a built exchange executable and the wheel carries it
in ``swatplus_bmi/_bin/``, tagged for the platform it was built on (``tools/wheel_tag.py``), so ``pip install
swatplus-bmi`` is all a user needs there. Without it the wheel is the pure one, and SWAT+ is built with
``swatplus-bmi build``."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class BundleSwat(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        exe = os.environ.get("SWATPLUS_BMI_BUNDLE")
        if self.target_name != "wheel" or not exe:
            return
        exe = Path(exe).resolve()
        if not exe.is_file():
            raise FileNotFoundError(f"SWATPLUS_BMI_BUNDLE: {exe} isn't a file")
        tag = subprocess.run([sys.executable, str(Path(self.root) / "tools" / "wheel_tag.py"), str(exe)],
                             check=True, capture_output=True, text=True).stdout.strip()
        build_data["force_include"][str(exe)] = f"swatplus_bmi/_bin/{exe.name}"
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{tag}"
