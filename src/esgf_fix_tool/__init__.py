"""Fix CMIP7 netCDF files from an esgqa report and a set of rules. CLI: ``esgf_fix_tool.cli.main:app``."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("esgf-fix-tool")
except PackageNotFoundError:  # running from a source tree
    __version__ = "0.0.0"
