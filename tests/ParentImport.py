import os
import sys
import pathlib

dirname = os.path.dirname(os.path.realpath(__file__))
parent_dir = str(pathlib.Path(dirname).parent.absolute())
sys.path.append(parent_dir)