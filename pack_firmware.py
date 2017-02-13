#!/usr/bin/env python
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

import argparse
import os
import sys
import uu

from chromite.lib import cros_build_lib
import chromite.lib.cros_logging as logging

class PackFirmware:
  """Handles building a shell-ball firmware update.

  Private members:
    _args: Parsed arguments.
  """
  def __init__(self, progname):
    self.script_base = os.path.dirname(progname)
    self.stub_file = os.path.join(self.script_base, 'pack_stub')
    self.pack_dist = os.path.join(self.script_base, 'pack_dist')
  
  # Parse the available arguments:
  # Invalid arguments or -h cause this function to print a message and exit.
  # Returns:
  #   argparse.Namespace object containing the attributes.
  def ParseArgs(self, argv):
    parser = argparse.ArgumentParser(
        description='Produce a firmware update shell-ball')
    parser.add_argument('-b', '--bios_image', type=str,
                        help='Path of input AP (BIOS) firmware image')
    parser.add_argument('-w', '--bios_rw_image', type=str,
                        help='Path of input BIOS RW firmware image')
    parser.add_argument('-e', '--ec_image', type=str,
                        help='Path of input Embedded Controller firmware image')
    parser.add_argument('-p', '--pd_image', type=str,
                        help='Path of input Power Delivery firmware image')
    parser.add_argument('--script', type=str, default='updater.sh',
                        help='File name of main script file')
    parser.add_argument('-o', '--output', type=str,
                        help='Path of output filename')
    parser.add_argument(
        '--extra', type=str,
        help='Directory list (separated by :) of files to be merged')

    parser.add_argument('--remove_inactive_updaters', action='store_true',
                        help='Remove inactive updater scripts')
    parser.add_argument('--create_bios_rw_image', action='store_true',
                        help='Resign and generate a BIOS RW image')
    merge_parser = parser.add_mutually_exclusive_group(required=False)
    merge_parser.add_argument(
        '--merge_bios_rw_image', default=True, action='store_true',
        help='Merge the --bios_rw_image into --bios_image RW sections')
    merge_parser.add_argument('--no-merge_bios_rw_image', action='store_false',
                        dest='merge_bios_rw_image',
                        help='Resign and generate a BIOS RW image')

    # stable settings
    parser.add_argument('--stable_main_version', type=str,
                        help='Version of stable main firmware')
    parser.add_argument('--stable_ec_version', type=str,
                        help='Version of stable EC firmware')
    parser.add_argument('--stable_pd_version', type=str,
                        help='Version of stable PD firmware')

    # embedded tools
    parser.add_argument('--tools', type=str,
        default='flashrom mosys crossystem gbb_utility vpd dump_fmap',
        help='List of tool programs to be bundled into updater')

    # TODO(sjg@chromium.org: Consider making this accumulate rather than using
    # the ':' separator.
    parser.add_argument(
        '--tool_base', type=str, default='',
        help='Default source locations for tools programs (delimited by colon)')
    return parser.parse_args(argv)

  def _HasCommand(self, cmd, package):
    result = cros_build_lib.RunCommand('type %s' % cmd, shell=True, quiet=True,
                                       error_code_ok=True)
    if result.returncode:
      logging.critical('You need %s (%s)' % (cmd, package))
      return False
    return True

  def _FindTool(self, tool):
    for path in self._args.tool_base.split(':'):
      fname = os.path.join(path, tool)
      if os.path.exists(fname):
        return os.path.abspath(fname)
    return None

  def _FindTools(self, tools):
    for tool in tools:
      if not self._FindTool(tool):
        logging.critical("Cannot find tool program '%s' to bundle" % tool)
        return False
    return True

  def Start(self, argv):
    self._args = self.ParseArgs(argv)
    main_script = os.path.join(self.pack_dist, self._args.script)

    if not self._HasCommand('shar', 'sharutils'):
      return False
    for fname in [main_script, self.stub_file]:
      if not os.path.exists(fname):
        logging.critical("Cannot find required file '%s'" % fname)
        return False
    if not self._FindTools(self._args.tools.split()):
      return False

    return True


# The style guide says that we cannot pass in sys.argv[0]. That mains testing
# a pain, so this is a full argv.
def main(argv):
  pack = PackFirmware(argv[0])
  return pack.Start(argv[1:])

if __name__ == "__main__":
  if not main(sys.argv):
    sys.exit(1)
