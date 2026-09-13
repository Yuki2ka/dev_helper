"""
path_args — shared path/argument resolver for small Python command modules.

Re-exports everything from ``command_paths`` so consumers can do::

    from path_args import resolve_paths, choose_output_file

The full public API is documented in ``command_paths.py``.
"""

from path_args.command_paths import *  # noqa: F401,F403
