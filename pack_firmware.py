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
import md5
import os
import re
import shutil
import sys
from StringIO import StringIO
import tempfile

from chromite.lib import cros_build_lib

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

  def _EnsureCommand(self, cmd, package):
    """Ensure that a command is available, raising an exception if not.

    Args:
      cmd: Command to check (just the name, not the full path).
      package: Name of package to install to obtain this tool.
    """
    result = cros_build_lib.RunCommand('type %s' % cmd, shell=True, quiet=True,
                                       error_code_ok=True)
    if result.returncode:
      raise PackError("You need '%s' (package '%s')" % (cmd, package))

  def _FindTool(self, tool):
    """Find a tool in the tool_base path list, raising an exception if missing.

    Args:
      tool: Name of tool to find (just the name, not the full path).
    """
    for path in self._args.tool_base.split(':'):
      fname = os.path.join(path, tool)
      if os.path.exists(fname):
        return os.path.realpath(fname)
    raise PackError("Cannot find tool program '%s' to bundle" % tool)

  def _CreateTmpDir(self):
    """Create a temporary directory, and remember it for later removal.

    Returns:
      Path name of temporary directory.
    """
    fname = tempfile.mkdtemp('.pack_firmware-%d' % os.getpid())
    self._tmp_dirs.append(fname)
    return fname

  def _RemoveTmpdirs(self):
    """Remove all the temporary directories."""
    for fname in self._tmp_dirs:
      shutil.rmtree(fname)
    self._tmp_dirs = []

  def _AddFlashromVersion(self):
    """Add flashrom version info to the collection of version information."""
    flashrom = self._FindTool('flashrom')

    # Look for a string ending in UTC.
    with open(flashrom, 'rb') as fd:
      data = fd.read()
      m = re.search(r'([0-9.]+ +: +[a-z0-9]+ +: +.+UTC)', data)
      if not m:
        raise PackError('Could not find flashrom version number')
      version = m.group(1)

    # crbug.com/695904: Can we use a SHA2-based algorithm?
    digest = md5.new()
    digest.update(data)
    result = cros_build_lib.RunCommand(['file', '-b', flashrom], quiet=True)
    print('\nflashrom(8): %s *%s\n             %s\n             %s\n' %
          (digest.hexdigest(), flashrom, result.output.strip(), version),
          file=self._versions)

  def _AddVersionInfo(self, name, fname, version):
    """Add version info for a single file.

    Calculates the MD5 hash of the file and adds this and other file details
    into the collection of version information.

    Args:
      name: User-readable name of the file (e.g. 'BIOS').
      fname: Filename to read.
      version: Version string (e.g. 'Google_Reef.9042.40.0').
    """
    if fname:
      with open(fname, 'rb') as fd:
        digest = md5.new()
        digest.update(fd.read())
      short_fname = re.sub(r'/build/.*/work/', '', fname)
      print('%s image:%s%s *%s' % (name, ' ' * (7 - len(name)),
                                   digest.hexdigest(), short_fname),
            file=self._versions)
    if version:
      print('%s version:%s%s' % (name, ' ' * (5 - len(name)), version),
            file=self._versions)

  def _ExtractFrid(self, image_file, section_name='RO_FRID'):
    """Extracts the firmware ID from an image file.

    Args:
      image_file: File to process.
      section_name: Name of the section of image_file which contains the
          firmware ID.

    Returns:
      Firmware ID as a string, if found, else ''
    """
    fname = os.path.join(self._tmpdir, section_name)

    # Remove any file that might be in the way (if not testing).
    if not self._testing and os.path.exists(fname):
      os.remove(fname)
    cros_build_lib.RunCommand(['dump_fmap', '-x', image_file], quiet=True,
                              cwd=self._tmpdir, error_code_ok=True)
    if os.path.exists(fname):
      with open(fname) as fd:
        return fd.read().strip().replace('\x00', '')
    return ''

  def Start(self, argv):
    """Handle the creation of a firmware shell-ball.

    argv: List of arguments (excluding the program name/argv[0]).

    Raises:
      PackError if any error occurs.
    """
    self._args = self.ParseArgs(argv)
    main_script = os.path.join(self._pack_dist, self._args.script)
    self._ec_version = self._args.ec_version

    self._EnsureCommand('shar', 'sharutils')
    for fname in [main_script, self._stub_file]:
      if not os.path.exists(fname):
        raise PackError("Cannot find required file '%s'" % fname)
    for tool in self._args.tools.split():
      self._FindTool(tool)
    if not any((self._args.bios_image, self._args.ec_image,
                self._args.pd_image)):
      raise PackError('Must assign at least one of BIOS or EC or PD image')
    # TODO(sjg@chromium.org): Add code to build shell-ball.


# The style guide says that we cannot pass in sys.argv[0]. That makes testing
# a pain, so this is a full argv.
def main(argv):
  global packer

  packer = FirmwarePacker(argv[0])
  packer.Start(argv[1:])

if __name__ == "__main__":
  main(sys.argv)
