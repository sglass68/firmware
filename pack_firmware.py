#!/usr/bin/env python2
# Copyright 2017 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Packages firmware images into an executale "shell-ball".

It requires:
 - at least one firmware image (*.bin, should be AP or EC or ...)
 - pack_dist/updater.sh main script
 - pack_stub as the template/stub script for output
 - any other additional files used by updater.sh in pack_dist folder
"""

from __future__ import print_function

import argparse
import os
import sys
from StringIO import StringIO

# For testing
packer = None


class PackError(Exception):
  """Exception returned by FirmwarePacker when something goes wrong"""
  pass


class FirmwarePacker(object):
  """Handles building a shell-ball firmware update.

  Most member functions raise an exception on error. This can be
  RunCommandError if an executed tool fails, or PackError on some other error.

  Private members:
    _args: Parsed arguments.
    _bios_version: Version string for BIOS (can be empty if none).
    _bios_rw_version: Version string for RW BIOS (can be empty if none).
    _ec_version: Version string for EC. Can be empty or 'IGNORE' if there is
        no EC firmware. Note from hungte@chromium.org: This is for backwards
        compatibility with updater2.sh since reinauer@chromium.org wanted a
        way to specify "we don't want to check version", which is useful for
        firmware having developer/normal parts in different blobs.
    _pd_version: Version string for PD. Can be empty or 'IGNORE' if there is
        no PD firmware.
        TODO(sjg@chromium.org): Do we have the same need for 'IGNORE' here?
        PD firmware was not supported in updater2.sh.
    _pack_dist: Path to 'pack_dist' directory.
    _script_base: Base directory with useful files (src/platform/firmware).
    _stub_file: Path to 'pack_stub'.
    _shflags_file: Path to shflags script.
    _testing: True if running tests.
    _basedir: Base temporary directory.
    _tmpdir: Temporary directory for use for running tools.
    _tmp_dirs: List of temporary directories created.
    _versions: Collected version information (StringIO).
  """

  def __init__(self, progname):
    # This may or may not provide the full path to the script, but in any case
    # we can access the script files using the same path as the script.
    self._script_base = os.path.dirname(progname)
    self._args = None
    self._bios_version = ''
    self._bios_rw_version = ''
    self._ec_version = 'IGNORE'
    self._pd_version = 'IGNORE'
    self._pack_dist = os.path.join(self._script_base, 'pack_dist')
    self._stub_file = os.path.join(self._script_base, 'pack_stub')
    self._shflags_file = os.path.join(self._script_base, 'lib/shflags/shflags')
    self._testing = False
    self._basedir = None
    self._tmpdir = None
    self._tmp_dirs = []
    self._versions = StringIO()

  def ParseArgs(self, argv):
    """Parse the available arguments.

    Invalid arguments or -h cause this function to print a message and exit.

    Args:
      argv: List of string arguments (excluding program name / argv[0])

    Returns:
      argparse.Namespace object containing the attributes.
    """
    parser = argparse.ArgumentParser(
        description='Produce a firmware update shell-ball')
    parser.add_argument('-b', '--bios_image', type=str,
                        help='Path of input AP (BIOS) firmware image')
    parser.add_argument('-w', '--bios_rw_image', type=str,
                        help='Path of input BIOS RW firmware image')
    parser.add_argument('-e', '--ec_image', type=str,
                        help='Path of input Embedded Controller firmware image')
    parser.add_argument('--ec_version', type=str,
                        help='Version of input EC firmware image (DEPRECATED)')
    parser.add_argument('-p', '--pd_image', type=str,
                        help='Path of input Power Delivery firmware image')
    parser.add_argument('--script', type=str, default='updater.sh',
                        help='File name of main script file')
    parser.add_argument('-o', '--output', type=str,
                        help='Path of output filename')
    parser.add_argument(
        '--extra', type=str,
        help='Directory list (separated by :) of files to be merged')

    arg_parser = parser.add_mutually_exclusive_group(required=False)
    arg_parser.add_argument(
        '--remove_inactive_updaters', default=True,
        action='store_true', help='Remove inactive updater scripts')
    arg_parser.add_argument(
        '--no-remove_inactive_updaters',
        action='store_false', dest='remove_inactive_updaters',
        help="Don't remove inactive updater scripts")

    parser.add_argument('--create_bios_rw_image', action='store_true',
                        help='Resign and generate a BIOS RW image')
    arg_parser = parser.add_mutually_exclusive_group(required=False)
    arg_parser.add_argument(
        '--merge_bios_rw_image', default=True, action='store_true',
        help='Merge the --bios_rw_image into --bios_image RW sections')
    arg_parser.add_argument(
        '--no-merge_bios_rw_image', action='store_false',
        dest='merge_bios_rw_image',
        help="Don't Merge the --bios_rw_image into --bios_image RW sections")

    # stable settings
    parser.add_argument('--stable_main_version', type=str,
                        help='Version of stable main firmware')
    parser.add_argument('--stable_ec_version', type=str,
                        help='Version of stable EC firmware')
    parser.add_argument('--stable_pd_version', type=str,
                        help='Version of stable PD firmware')

    # embedded tools
    parser.add_argument(
        '--tools', type=str,
        default='flashrom mosys crossystem gbb_utility vpd dump_fmap',
        help='List of tool programs to be bundled into updater')

    # TODO(sjg@chromium.org: Consider making this accumulate rather than using
    # the ':' separator.
    parser.add_argument(
        '--tool_base', type=str, default='',
        help='Default source locations for tools programs (delimited by colon)')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Avoid output except for warnings/errors')
    return parser.parse_args(argv)

  def Start(self, argv):
    """Handle the creation of a firmware shell-ball.

    argv: List of arguments (excluding the program name/argv[0]).

    Raises:
      PackError if any error occurs.
    """
    self._args = self.ParseArgs(argv)
    print(self._args)


# The style guide says that we cannot pass in sys.argv[0]. That makes testing
# a pain, so this is a full argv.
def main(argv):
  global packer

  packer = FirmwarePacker(argv[0])
  packer.Start(argv[1:])

if __name__ == "__main__":
  main(sys.argv)
