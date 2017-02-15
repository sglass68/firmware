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

from __future__ import print_function

import argparse
import codecs
import collections
import glob
import md5
import os
import re
import shutil
import sys
import tempfile
import uu

try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO

from chromite.lib import cros_build_lib
import chromite.lib.cros_logging as logging
from chromite.lib import osutils

sys.path.append('utils')
import merge_file

IMAGE_MAIN = 'bios.bin'
IMAGE_MAIN_RW = 'bios_rw.bin'
IMAGE_EC = 'ec.bin'
IMAGE_PD = 'pd.bin'
Section = collections.namedtuple('Section', ['offset', 'size'])

class PackError(Exception):
  pass

class PackFirmware:
  """Handles building a shell-ball firmware update.

  Private members:
    _args: Parsed arguments.
    _pack_dist: Path to 'pack_dist' directory.
    _script_base: Base directory with useful files (src/platform/firmware).
    _stub_file: Path to 'pack_stub'.
    _tmpbase: Base temporary directory.
    _tmpdir: Temporary directory for use for running tools.
    _tmp_dirs: List of temporary directories created.
    _versions: Collected version information (StringIO).
  """

  def __init__(self, progname):
    self._script_base = os.path.dirname(progname)
    self._stub_file = os.path.join(self._script_base, 'pack_stub')
    self._pack_dist = os.path.join(self._script_base, 'pack_dist')
    self._shflags_file = os.path.join(self._script_base, 'lib/shflags/shflags')
    self._tmp_dirs = []
    self._versions = StringIO()
    self._bios_version = ''
    self._bios_rw_version = ''
    self._ec_version = ''
    self._pd_version = ''
 
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

  def _EnsureCommand(self, cmd, package):
    """Ennsure that a command is available, raising an exception if not.

    Args:
      cmd: Command to check (just the name, not the full path.
    Raises:
      PackError if the command is not available.
    """
    result = cros_build_lib.RunCommand('type %s' % cmd, shell=True, quiet=True,
                                       error_code_ok=True)
    if result.returncode:
      raise PackError("You need '%s' (package '%s')" % (cmd, package))

  def _FindTool(self, tool):
    """Find a tool in the tool_base path list, raising an exception if missing.

    Args:
      tool: Name pf tool to find (just the name, not the full path.
    Raises:
      PackError if the tool is not available.
    """
    for path in self._args.tool_base.split(':'):
      fname = os.path.join(path, tool)
      if os.path.exists(fname):
        return os.path.realpath(fname)
    raise PackError("Cannot find tool program '%s' to bundle" % tool)

  def _EnsureTools(self, tools):
    """Ensure that all required tools are available.

    Args:
      tools: List of tools to check.
    Raises:
      PackError if any tool is not available.
    """
    for tool in tools:
      self._FindTool(tool)

  def _GetTmpdir(self):
    """Get a temporary directory, and remember it for later removal.

    Returns:
      Path name of temporary directory.
    """
    fname = tempfile.mkdtemp('pack_firmware-%d' % os.getpid())
    self._tmp_dirs.append(fname)
    return fname

  def _RemoveTmpdirs(self):
    """Remove all the temporary directories"""
    for fname in self._tmp_dirs:
      shutil.rmtree(fname)
    self._tmpdirs = []

  def _AddFlashromVersion(self):
    """Add flashrom version info to the collection of version information."""
    flashrom = self._FindTool('flashrom')

    # Look for a string ending in UTC.
    with open(flashrom, 'rb') as fd:
      data = fd.read()
      end = data.find('UTC\0')
      pos = end
      while data[pos - 1] >= ' ' and data[pos - 1] < chr(127):
        pos -= 1
      version = data[pos:end + 3]
    hash = md5.new()
    hash.update(data)
    result = cros_build_lib.RunCommand(['file', '-b', flashrom], quiet=True)
    print('flashrom(8): %s *%s\n             %s\n             %s\n' %
        (hash.hexdigest(), flashrom, result.output.strip(), version),
        file=self._versions)

  def _AddVersionInfo(self, name, fname, version):
    """Add version info for a single file.

    Calculates the md5 hash of the file and adds this and other file details
    into the collection of version information.

    Args:
      name: User-readable name of the file (e.g. 'BIOS')
      fname: Filename to read
      version: Version string (e.g. 'Google_Reef.9042.40.0')
    """
    with open(fname, 'rb') as fd:
      hash = md5.new()
      hash.update(fd.read())
    short_fname = re.sub('/build/.*/work/', '*', fname)
    print('%s image:%s%s %s' % (name, ' ' * (7 - len(name)), hash.hexdigest(),
                                short_fname), file=self._versions)
    if version:
      print('%s version:%s%s' % (name, ' ' * (7 - len(name)), version),
            file=self._versions)

  def _ExtractFrid(self, image_file, default_frid='', section_name='RO_FRID'):
    """Extracts the firmware ID from an image file.

    Args:
      image_file: File to process.
      default_frid: Default firmware ID if we cannot obtain one.
      section_name: Name of the section of image_file which contains the
          firmware ID.

    Returns:
      Firmware ID as a string, if found, else default_frid
    """
    cros_build_lib.RunCommand(['dump_fmap', '-x', self._args.bios_image],
                              quiet=True, cwd=self._tmpdir, error_code_ok=True)
    fname = os.path.join(self._tmpdir, section_name)
    if os.path.exists(fname):
      with open(fname) as fd:
        return fd.read().strip().replace('\x00', '')
    return default_frid

  def _BaseFilename(self, leafname):
    """Build a filename in the temporary base directory.

    Args:
      leafname: Leafname (with no directory) of file to build.
    Returns:
      New filename within the self._tmpbase directory.
    """
    return os.path.join(self._tmpbase, leafname)

  def _GetPreambleFlags(self, fname):
    cros_build_lib.RunCommand(['dump_fmap', '-x', fname],
                              quiet=True, cwd=self._tmpdir)
    cros_build_lib.RunCommand(['gbb_utility', '--rootkey=rootkey.bin', 'GBB'],
                              quiet=True, cwd=self._tmpdir)
    result = cros_build_lib.RunCommand(
        ['vbutil_firmware', '--verify VBLOCK_A', '--signpubkey', 'rootkey.bin',
         '--fv', 'FW_MAIN_A'], quiet=True, cwd=self._tmpdir)
    lines = ([line for line in result.output.splitlines()
              if 'Preamble flags' in line])
    if len(lines) != 1:
      raise PackError("vbutil_firmware returned %d 'Preamble flags' lines",
                      len(lines))
    return int(lines[0].split()[-1])

  def _SetPreambleFlags(self, infile, outfile, preamble_flags):
    keydir = '/usr/share/vboot/devkeys'
    cros_build_lib.RunCommand(
        ['resign_firmwarefd.sh', infile, outfile,
         os.path.join(keydir, 'firmware_data_key.vbprivk'),
         os.path.join(keydir, 'firmware.keyblock'),
         os.path.join(keydir, 'dev_firmware_data_key.vbprivk'),
         os.path.join(keydir, 'dev_firmware.keyblock'),
         os.path.join(keydir, 'kernel_subkey.vbpubk')],
        quiet=True, cwd=self._tmpdir)

  def _CreateRwFirmware(self, ro_fname, rw_fname):
    preamble_flags = self._GetPreambleFlags(ro_fname)
    if not (preamble_flags & 1):
      raise PackError("Firmware image '%s' is NOT RO_NORMAL firmware" %
                      ro_fname)
    self._SetPreambleFlags(ro_fname, rw_fname, preamble_flags ^ 1)
    print("RW firmware image '%s' created" % rw_fname)

  def _CheckRwFirmeare(self, fname):
    if not (self._GetPreambleFlags(fname) & 1):
      raise PackError("Firmware image '%s' is NOT RW-firmware" % fname)

  def _GetFmap(self, fname):
    result = cros_build_lib.RunCommand(['dump_fmap', '-p', fname],
                                       quiet=True, cwd=self._tmpdir)
    sections = {}
    for line in result.output.splitlines():
      name, offset, size = line.split()
      sections[name] = Section(int(offset), int(size))
    return sections

  def _CloneFirmwareSection(self, src, dst, section):
    src_section = self._GetFmap(src)[section]
    dst_section = self._GetFmap(dst)[section]
    if not src_section.size:
      raise PackError("Firmware section '%s' is invalid" % section)
    if src_section.size != dst_section.size:
      raise PackError("Firmware section '%s' size is different, cannot clone" %
                      section)
    if src_section.offset != dst_section.offset:
      raise PackError("Firmware section '%s' is not in same location, cannot "
                      "clone" % section)
    merge_file.merge_file(dst, src, dst_section.offset, src_section.offset,
                          src_section.size)

  def _MergeRwFirmware(self, ro_fname, rw_fname):
    self._CloneFirmwareSection(rw_fname, ro_fname, 'RW_SECTION_A')
    self._CloneFirmwareSection(rw_fname, ro_fname, 'RW_SECTION_B')

  def _ExtractEcRcFmap(self, fname, ecrw_fname):
    result = cros_build_lib.RunCommand(['dump_fmap', '-x', fname, 'EC_MAIN_A'],
                                       quiet=True, cwd=self._tmpdir)
    ec_main_a = os.path.join(self._tmpdir, 'EC_MAIN_A')
    with open(ec_main_a) as fd:
      count, offset, size = struct.unpack('<III', fd.read(12))
    if count != 1 or offset != 12:
      raise PackError('Unexpected EC_MAIN_A (%d, %d). Cannot merge EC RW' %
                      count, offset)
    # To make sure files to be merged are both prepared, merge_file.py will
    # only accept existing files, so we have to create ecrw now.
    osutils.Touch(ecrw_fname)
    merge_file.merge_file(ecrw_fname, ec_main_a, 0, offset, size)

  def _ExtractEcRcCbfs(self, fname, cbfs_name, ecrw_fname):
    result = cros_build_lib.RunCommand(
        ['cbfstool', fname, 'extract', '-n', cbfs_name, '-f', ecrw_fname, 'r',
         'FW_MAIN_A'], quiet=True, cwd=self._tmpdir)

  def _ExtractEcRw(self, fname, cbfs_name, ecrw_fname):
    if 'EC_MAIN_A' in self._GetFmap(fname):
      self._ExtractEcRcFmap(fname, ecrw_fname)
    else:
      self._ExtractEcRcCbfs(fname, cbfs_name, ecrw_fname)

  def _MergeRwEcFirmware(self, ec_fname, rw_fname, cbfs_name):
    ecrw_fname = os.path.join(self._tmpdir, 'ecrw')
    self._ExtractEcRw(rw_fname, cbfs_name, ecrw_fname)
    section = self._GetFmap(ec_fname)['EC_RW']
    if section.size > os.stat(ecrw_fname).st_size:
      raise PackError('New RW payload larger than preserved FMAP section, '
                      'cannot merge')
    merge_file.merge_file(ec_fname, ecrw_fname, section.offset)

  def _CopyFirmwareFiles(self):
    bios_rw_bin = self._args.bios_rw_image
    if self._args.bios_image:
      self._bios_version = self._ExtractFrid(self._args.bios_image)
      self._bios_rw_version = self._bios_version
      shutil.copy2(self._args.bios_image, self._BaseFilename(IMAGE_MAIN))
      self._AddVersionInfo('BIOS', self._args.bios_image, self._bios_version)
    else:
      self._args.merge_bios_rw_image = False

    if not bios_rw_bin and self._args.create_bios_rw_image:
      bios_rw_bin = self._BaseFilename(IMAGE_MAIN_RW)
      self._CreateRwFirmware(self._args.bios_image, bios_rw_bin)
      self._args.merge_bios_rw_image = False

    if bios_rw_bin:
      self._CheckRwFirmeare(bios_rw_bin)
      self._bios_rw_version = self._ExtractFrid(bios_rw_bin)
      if self._args.merge_bios_rw_image:
        self._MergeRwFirmware(self._BaseFilename(IMAGE_MAIN), bios_rw_bin)
      elif bios_rw_bin != self._BaseFilename(IMAGE_MAIN_RW):
        shutil.copy2(bios_rw_bin, self._BaseFilename(IMAGE_MAIN_RW))
      self._AddVersionInfo('BIOS (RW)', self._args.bios_image,
                           self._bios_rw_version)
    else:
      self._args.merge_bios_rw_image = False

    if self._args.ec_image:
      self._ec_version = self._ExtractFrid(self._args.ec_image)
      shutil.copy2(self._args.ec_image, self._BaseFilename(IMAGE_EC))
      self._AddVersionInfo('EC', self._args.ec_image, self._ec_version)
      if self._args.merge_bios_rw_image:
        self._MergeRwEcFirmware(self._BaseFilename(IMAGE_EC),
                                self._BaseFilename(IMAGE_MAIN), 'ecrw')
        ec_rw_version = self._ExtractFrid(self._BaseFilename(IMAGE_EC), '', 'RW_FWID')
        print('EC (RW) version: %s' % ec_rw_version, file=self._versions)

    if self._args.pd_image:
      self._pd_version = self._ExtractFrid(self._args.pd_image)
      shutil.copy2(self._args.pd_image, self._BaseFilename(IMAGE_PD))
      self._AddVersionInfo('PD', self._args.pd_image, self._pd_version)
      if self._args.merge_bios_rw_image:
        self._MergeRwEcFirmware(self._BaseFilename(IMAGE_PD),
                                self._BaseFilename(IMAGE_MAIN), 'pdrw')
        pd_rw_version = self._ExtractFrid(self._BaseFilename(IMAGE_PD), '', 'RW_FWID')
        print('PD (RW) version: %s' % pd_rw_version, file=self._versions)

  def _CopyExecutable(self, src, dst):
    if os.path.isdir(dst):
      dst = os.path.join(dst, os.path.basename(src))
    shutil.copy2(src, dst)
    os.chmod(dst, os.stat(dst).st_mode | 0555)

  def _CopyReadable(self, src, dst):
    if os.path.isdir(dst):
      dst = os.path.join(dst, os.path.basename(src))
    shutil.copy2(src, dst)
    os.chmod(dst, os.stat(dst).st_mode | 0444)

  def _BuildShellball(self):
    def _CopyBaseFiles():
      self._CopyReadable(self._shflags_file, self._tmpbase)
      for tool in self._args.tools.split():
        tool_fname = self._FindTool(tool)
        if os.path.exists(tool_fname + '_s'):
          tool_fname += '_s'
        self._CopyExecutable(tool_fname, self._BaseFilename(tool))
      for fname in glob.glob(os.path.join(self._pack_dist, '*')):
        if (self._args.remove_inactive_updaters and 'updater' in fname and
            not self._args.script in fname):
          continue
        self._CopyExecutable(fname, self._tmpbase)

    def _CopyExtraFiles():
      if self._args.extra:
        for extra in self._args.extra:
          if os.path.isdir(extra):
            fnames = glob.glob(os.path.join(extra, '/*'))
            if not fnames:
              raise PackError("cannot copy extra files from folder '%s'" % extra)
            for fname in fnames:
              self._CopyReadable(fname, os.path.join(self._tmpbase, fname))
            print("Extra files from directory '%s'" % extra,
                  file=self._versions)
          else:
            self._CopyReadable(extra, os.path.join(self._tmpbase, extra))
            print("Extra file '%s'" % extra, file=self._versions)

    def _WriteUpdateScript():
      with open(self._stub_file) as fd:
        data = fd.read()
      replace_dict = {
        'REPLACE_RO_FWID': self._bios_version,
        'REPLACE_FWID': self._bios_rw_version,
        'REPLACE_ECID': self._ec_version,
        'REPLACE_PDID': self._pd_version,
        # Set platform to first field of firmware version
        # (ex: Google_Link.1234 -> Google_Link).
        'REPLACE_PLATFORM': self._bios_version.split('.')[0],
        'REPLACE_SCRIPT': self._args.script,
        'REPLACE_STABLE_FWID': self._args.stable_main_version,
        'REPLACE_STABLE_ECID': self._args.stable_ec_version,
        'REPLACE_STABLE_PDID': self._args.stable_pd_version,
        }
      rep = dict((re.escape(k), v) for k, v in replace_dict.iteritems())
      pattern = re.compile("|".join(rep.keys()))
      data = pattern.sub(lambda m: rep[re.escape(m.group(0))], data)

      fname = self._args.output
      with open(fname, 'w') as fd:
        fd.write(data)
      os.chmod(fname, os.stat(fname).st_mode | 0555)

    def _WriteVersionFile():
      with open(self._BaseFilename('VERSION'), 'w') as fd:
        fd.write(self._versions.getvalue())

    _CopyBaseFiles()
    _CopyExtraFiles()
    _WriteUpdateScript()
    _WriteVersionFile()

    result = cros_build_lib.RunCommand(
        ['sh', self._args.output, '--sb_repack', self._tmpbase], combine_stdout_stderr=True, mute_output=False)
    print(result.output)
    with open(self._BaseFilename('VERSION')) as fd:
      print(fd.read())
    #result = cros_build_lib.RunCommand(
        #'sh %s --sb_repack %s' % (self._args.output, self._tmpbase), shell=True)

    print("\nPacked output image is '%s'" % self._args.output)

  #with open(os.path.join(self._tmpbase, 'VERSION'), 'w'):

  def Start(self, argv):
    """Handle the creation of a firmware shell-ball.

    argv: List of arguments (excluding the program name/argv[0]).

    Raises:
      PackError if any error occurs.
    """
    self._args = self.ParseArgs(argv)
    main_script = os.path.join(self._pack_dist, self._args.script)

    self._EnsureCommand('shar', 'sharutils')
    for fname in [main_script, self._stub_file]:
      if not os.path.exists(fname):
        raise PackError("Cannot find required file '%s'" % fname)
    self._EnsureTools(self._args.tools.split())
    if (not self._args.bios_image and not self._args.ec_image and
        not self._args.pd_image):
      raise PackError('Must assign at least one of BIOS or EC or PD image')
    try:
      if not self._args.output:
        raise PackError('Missing output file')
      self._tmpbase = self._GetTmpdir()
      self._tmpdir = self._GetTmpdir()
      self._AddFlashromVersion()
      self._CopyFirmwareFiles()
      self._BuildShellball()
    finally:
      self._RemoveTmpdirs()

# The style guide says that we cannot pass in sys.argv[0]. That makes testing
# a pain, so this is a full argv.
def main(argv):
  global pack

  pack = PackFirmware(argv[0])
  pack.Start(argv[1:])

if __name__ == "__main__":
  if not main(sys.argv):
    sys.exit(1)
