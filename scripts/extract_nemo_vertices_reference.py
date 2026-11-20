#!/usr/bin/env python3
"""Extract reviewed hfx/hfy/areacello vertices without copying scientific fields."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from esgf_fix_tool.fixes.nemo_vertices import extract_reference

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hfx', type=Path, required=True)
    parser.add_argument('--hfy', type=Path, required=True)
    parser.add_argument('--areacello', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(extract_reference({'U': args.hfx, 'V': args.hfy, 'T': args.areacello}, args.output))
