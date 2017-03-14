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
import collections
import glob
import md5
import os
import re
import shutil
import struct
import sys
from StringIO import StringIO
import tempfile

from chromite.lib import cros_build_lib
from chromite.lib import osutils

sys.path.append('utils')
import merge_file

IMAGE_MAIN = 'bios.bin'
IMAGE_MAIN_RW = 'bios_rw.bin'
IMAGE_EC = 'ec.bin'
IMAGE_PD = 'pd.bin'
IGNORE = 'IGNORE'
Section = collections.namedtuple('Section', ['offset', 'size'])
ImageFile = collections.namedtuple('ImageFile', ['filename', 'version'])

# File execution permissions. We could use state.S_... but that's confusing.
CHMOD_ALL_READ = 0444
CHMOD_ALL_EXEC = 0555

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

  def _FindTool(self, tool_base, tool):
    """Find a tool in the tool_base path list, raising an exception if missing.

    Args:
      tool_base: List of directories to check.
      tool: Name of tool to find (just the name, not the full path).
    """
    for path in tool_base:
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

  def _AddFlashromVersion(self, tool_base):
    """Add flashrom version info to the collection of version information.

    Args:
      tool_base: List of directories to check.
    """
    flashrom = self._FindTool(tool_base, 'flashrom')

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
    print('\nflashrom(8): %s *%s\n             %s\n             %s' %
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

      # Modify the filename to replace any use of our base directory with a
      # constant string, so we produce the same output on each run. Also drop
      # the build directory since it is not useful to the user.
      short_fname = fname
      if self._basedir:
        short_fname = short_fname.replace(
            self._basedir,
            os.path.join(os.path.dirname(self._basedir), 'tmp'))
      short_fname = re.sub(r'/build/.*/work/', '', short_fname)
      print('%s image:%s%s *%s' % (name, ' ' * max(3, 7 - len(name)),
                                   digest.hexdigest(), short_fname),
            file=self._versions)
    # b/36104199 Handling of IGNORE is a work-around required for x86-mario.
    if version and version != IGNORE:
      print('%s version:%s%s' % (name, ' ' * max(1, 5 - len(name)), version),
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
        return fd.read().replace('\x00', '').strip()
    return ''

  def _BaseDirPath(self, basename):
    """Build a filename in the temporary base directory.

    Args:
      basename: Leafname (with no directory) of file to build.

    Returns:
      New filename within the self._basedir directory.
    """
    return os.path.join(self._basedir, basename)

  def _GetPreambleFlags(self, fname):
    """Get the preamble flags from an image.

    Args:
      fname: Image to check (relative or absolute path).

    Returns:
      Preamble flags as an integer. See VB2_FIRMWARE_PREAMBLE_... for available
      flags; the most common one is VB2_FIRMWARE_PREAMBLE_USE_RO_NORMAL.
    """
    cros_build_lib.RunCommand(['dump_fmap', '-x', fname],
                              quiet=True, cwd=self._tmpdir)
    cros_build_lib.RunCommand(['gbb_utility', '--rootkey=rootkey.bin', 'GBB'],
                              quiet=True, cwd=self._tmpdir)
    result = cros_build_lib.RunCommand(
        ['vbutil_firmware', '--verify', 'VBLOCK_A', '--signpubkey',
         'rootkey.bin', '--fv', 'FW_MAIN_A'], quiet=True, cwd=self._tmpdir)
    lines = ([line for line in result.output.splitlines()
              if 'Preamble flags' in line])
    if len(lines) != 1:
      raise PackError("vbutil_firmware returned %d 'Preamble flags' lines",
                      len(lines))
    return int(lines[0].split()[-1])

  def _SetPreambleFlags(self, infile, outfile, preamble_flags):
    """Set the preamble flags for an image.

    Args:
      infile: Input image file (relative or absolute path).
      outfile: Output image file (relative or absolute path).
      preamble_flags: Preamble flags as an integer.
    """
    keydir = '/usr/share/vboot/devkeys'
    cros_build_lib.RunCommand(
        ['resign_firmwarefd.sh', infile, outfile,
         os.path.join(keydir, 'firmware_data_key.vbprivk'),
         os.path.join(keydir, 'firmware.keyblock'),
         os.path.join(keydir, 'dev_firmware_data_key.vbprivk'),
         os.path.join(keydir, 'dev_firmware.keyblock'),
         os.path.join(keydir, 'kernel_subkey.vbpubk'),
         '0', str(preamble_flags)],
        quiet=True, cwd=self._tmpdir)

  def _CopyTimestamp(self, reference_fname, fname):
    """Copy the timestamp from a reference file to another file.

    reference_fname: Reference file for timestamp.
    fname: File to copy timestamp to.
    """
    mtime = os.stat(reference_fname).st_mtime
    os.utime(fname, (mtime, mtime))

  def _CreateRwFirmware(self, ro_fname, rw_fname):
    """Build a RW firmware file from an input RO file.

    This works by clearing bit 0 of the preamble flags, this indicating this is
    RW firmware.

    Args:
      ro_fname: Filename of RO firmware (relative or absolute path).
      rw_fname: Filename of RW firmware (relative or absolute path).
    """
    preamble_flags = self._GetPreambleFlags(ro_fname)
    if not (preamble_flags & 1):
      raise PackError("Firmware image '%s' is NOT RO_NORMAL firmware" %
                      ro_fname)
    self._SetPreambleFlags(ro_fname, rw_fname, preamble_flags ^ 1)
    self._CopyTimestamp(ro_fname, rw_fname)
    if not self._args.quiet:
      print("RW firmware image '%s' created" % rw_fname)

  def _CheckRwFirmware(self, fname):
    """Check that the firmware file is RW firmware.

    Raises:
      PackError or RunCommandError if the flags could not be read or indicate
        that the firmware file is not RW firmware.
    """
    if self._GetPreambleFlags(fname) & 1:
      raise PackError("Firmware image '%s' is NOT RW-firmware" % fname)

  def _GetFMAP(self, fname):
    """Get the FMAP (flash map) from a firmware image.

    Args:
      fname: Filename of firmware image.

    Returns:
      A dict comprising:
        key: Section name.
        value: Section() named tuple containing offset and size.
    """
    result = cros_build_lib.RunCommand(['dump_fmap', '-p', fname],
                                       quiet=True, cwd=self._tmpdir)
    sections = {}
    for line in result.output.splitlines():
      name, offset, size = line.split()
      sections[name] = Section(int(offset), int(size))
    return sections

  def _CloneFirmwareSection(self, dst, src, section):
    """Clone a section in one file from another.

    Args:
      dst: Destination file (relative or absolute path).
      src: Source file (relative or absolute path).
      section: Section to clone.
    """
    src_section = self._GetFMAP(src)[section]
    dst_section = self._GetFMAP(dst)[section]
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
    """Merge RW sections from RW firmware to RO firmware.

    Args:
      ro_fname: RO firmware image file (relative or absolute path).
      rw_fname: RW firmware image file (relative or absolute path).
    """
    self._CloneFirmwareSection(ro_fname, rw_fname, 'RW_SECTION_A')
    self._CloneFirmwareSection(ro_fname, rw_fname, 'RW_SECTION_B')
    self._CopyTimestamp(rw_fname, ro_fname)

  def _ExtractEcRwUsingFMAP(self, fname, ecrw_fname):
    """Use the FMAP to extract the EC_MAIN_A section containing an EC binary.

    Args:
      fname: Filename of firmware image (relative or absolute path).
      ecrw_fname: Filename to put EC binary into (relative or absolute path).
    """
    cros_build_lib.RunCommand(['dump_fmap', '-x', fname, 'EC_MAIN_A'],
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

  def _ExtractEcRwUsingCBFS(self, fname, cbfs_name, ecrw_fname):
    """Extract an EC binary from a CBFS image.

    Args:
      fname: Filename of firmware image (relative or absolute path).
      cbfs_name: Name of file in CBFS which contains the EC binary.
      ecrw_fname: Filename to put EC binary into (relative or absolute path).
    """
    cros_build_lib.RunCommand(
        ['cbfstool', fname, 'extract', '-n', cbfs_name, '-f', ecrw_fname, '-r',
         'FW_MAIN_A'], quiet=True, cwd=self._tmpdir)

  def _ExtractEcRw(self, fname, cbfs_name, ecrw_fname):
    """Obtain the RW EC binary.

    Args:
      fname: Filename of firmware image (relative or absolute path).
      cbfs_name: Name of file in CBFS which contains the EC binary, if the
          image does not have an EC_MAIN_A section.
      ecrw_fname: Filename to put EC binary into (relative or absolute path).

    Raises:
      PackError or RunCommandError if an error occurs.
    """
    if 'EC_MAIN_A' in self._GetFMAP(fname):
      self._ExtractEcRwUsingFMAP(fname, ecrw_fname)
    else:
      self._ExtractEcRwUsingCBFS(fname, cbfs_name, ecrw_fname)

  def _MergeRwEcFirmware(self, ec_fname, rw_fname, cbfs_name):
    """Merge EC firmware from an image into the given file.

    Args:
      ec_fname: Filename to merge RW EC binary into (relative or absolute path).
      rw_fname: Filename of firmware image (relative or absolute path).
      cbfs_name: Name of file in CBFS which contains the EC binary, if the
          image does not have an EC_MAIN_A section.
    """
    ecrw_fname = os.path.join(self._tmpdir, 'ecrw')
    self._ExtractEcRw(rw_fname, cbfs_name, ecrw_fname)
    section = self._GetFMAP(ec_fname)['EC_RW']
    ecrw_size = os.stat(ecrw_fname).st_size
    if section.size < ecrw_size:
      raise PackError('New RW payload size %#x is larger than preserved FMAP '
                      'section %#x, cannot merge' % (section.size, ecrw_size))
    merge_file.merge_file(ec_fname, ecrw_fname, section.offset)
    self._CopyTimestamp(rw_fname, ec_fname)

  def _CopyFirmwareFiles(self, bios_image, bios_rw_image, ec_image, pd_image,
                         default_ec_version, create_bios_rw_image,
                         image_main, image_main_rw, image_ec, image_pd):
    """Process firmware files and copy them into the working directory.

    Args:
      bios_image: Input filename of main BIOS (main) image.
      bios_rw_image: Input filename of RW BIOS image, or None if none.
      ec_image: Input filename of EC (Embedded Controller) image.
      pd_image: Input filename of PD (Power Delivery) image.
      default_ec_version: Default EC version string to use if no version
          can be detected in ec_image.
      create_bios_rw_image: True to create a RW BIOS image from the main one
          if not provided.
      image_main: Output filename to use for main image.
      image_main_rw: Output filename to use for main RW image.
      image_ec: Output filename to use for EC image.
      image_pd: Output filename to use for PDimage.

    Returns:
      Dict containing information on the output image files:
        key: Name, one of BIOS, BIOS (RW), EC, EC (RW), PD, PD (RW).
        value: ImageFile, containing filename and version.
    """
    image_files = {}
    merge_bios_rw_image = self._args.merge_bios_rw_image
    if bios_image:
      bios_version = self._ExtractFrid(bios_image)
      # b/36104199 This work-around is required for x86-mario.
      if not bios_version:
        bios_version = IGNORE
      bios_rw_version = bios_version
      shutil.copy2(bios_image, image_main)
      image_files['BIOS'] = ImageFile(bios_image, bios_version)
    else:
      merge_bios_rw_image = False

    if not bios_rw_image and create_bios_rw_image:
      bios_rw_image = image_main_rw
      self._CreateRwFirmware(bios_image, bios_rw_image)
      merge_bios_rw_image = False

    if bios_rw_image:
      self._CheckRwFirmware(bios_rw_image)
      bios_rw_version = self._ExtractFrid(bios_rw_image)
      if merge_bios_rw_image:
        self._MergeRwFirmware(image_main, bios_rw_image)
      elif bios_rw_image != image_main_rw:
        shutil.copy2(bios_rw_image, image_main_rw)
      image_files['BIOS (RW)'] = ImageFile(bios_rw_image, bios_rw_version)
    else:
      merge_bios_rw_image = False

    if ec_image:
      ec_version = self._ExtractFrid(ec_image)
      if not ec_version and default_ec_version:
        ec_version = default_ec_version
      image_files['EC'] = ImageFile(ec_image, ec_version)
      shutil.copy2(ec_image, image_ec)
      if merge_bios_rw_image:
        self._MergeRwEcFirmware(image_ec, image_main, 'ecrw')
        ec_rw_version = self._ExtractFrid(image_ec, 'RW_FWID')
        image_files['EC (RW)'] = ImageFile(None, ec_rw_version)

    if pd_image:
      pd_version = self._ExtractFrid(pd_image)
      image_files['PD'] = ImageFile(pd_image, pd_version)
      shutil.copy2(pd_image, image_pd)
      if merge_bios_rw_image:
        self._MergeRwEcFirmware(image_pd, image_main, 'pdrw')
        pd_rw_version = self._ExtractFrid(image_pd, 'RW_FWID')
        image_files['PD (RW)'] = ImageFile(None, pd_rw_version)
    return image_files

  def _WriteVersions(self, image_files):
    """Write version information for all image files into the version file.

    image_files: Dict with:
        key: Image type (e.g. 'BIOS').
        value: ImageFile object containing filename and version.
    """
    print(file=self._versions)  # Blank line.
    for name in sorted(image_files.keys()):
      image_file = image_files[name]
      self._AddVersionInfo(name, image_file.filename, image_file.version)

  def _CopyFile(self, src, dst, mode=CHMOD_ALL_READ):
    """Copy a file (to another file or into a directory) and set its mode.

    src: Source filename (relative or absolute path).
    dst: Destination filename or directory (relative or absolute path).
    mode: File mode to OR with the existing mode.
    """
    if os.path.isdir(dst):
      dst = os.path.join(dst, os.path.basename(src))
    shutil.copy2(src, dst)
    os.chmod(dst, os.stat(dst).st_mode | mode)

  def _CopyBaseFiles(self, tool_base, tools, script):
    """Copy base files that every firmware update needs.

    Args:
      tool_base: List of directories to check.
      tools: List of tools to copy.
    """
    self._CopyFile(self._shflags_file, self._basedir)
    for tool in tools:
      tool_fname = self._FindTool(tool_base, tool)
      # Most tools are dynamically linked, but if there is a statically
      # linked version (denoted by a '_s' suffix) use that in preference.
      # This helps to reduce run-time dependencies for firmware update,
      # which is a critical process.
      if os.path.exists(tool_fname + '_s'):
        tool_fname += '_s'
      self._CopyFile(tool_fname, self._BaseDirPath(tool), CHMOD_ALL_EXEC)
    for fname in glob.glob(os.path.join(self._pack_dist, '*')):
      if (self._args.remove_inactive_updaters and 'updater' in fname and
          not script in fname):
        continue
      self._CopyFile(fname, self._basedir, CHMOD_ALL_EXEC)

  def _CopyExtraFiles(self, extras):
    """Copy extra files into the base directory.

    extras: List of extra files / directories to copy. If the item is a
        directory, then the files in that directory are copied into the
        base directory.
    """
    for extra in extras:
      if os.path.isdir(extra):
        fnames = glob.glob(os.path.join(extra, '*'))
        if not fnames:
          raise PackError("cannot copy extra files from folder '%s'" %
                          extra)
        for fname in fnames:
          self._CopyFile(fname, self._basedir)
        print('Extra files from folder: %s' % extra,
              file=self._versions)
      else:
        self._CopyFile(extra, self._basedir)
        print('Extra file: %s' % extra, file=self._versions)

  def _CreateFileFromTemplate(self, infile, outfile, replace_dict):
    """Create a new file based on a template and some string replacements.

    Args:
      infile: Input file (the template).
      outfile: Output file.
      replace_dict: Dict with:
          key: String to replace.
          value: Value to replace with.
    """
    with open(infile) as fd:
      data = fd.read()
    rep = dict((re.escape(k), v) for k, v in replace_dict.iteritems())
    pattern = re.compile("|".join(rep.keys()))
    data = pattern.sub(lambda m: rep[re.escape(m.group(0))], data)

    with open(outfile, 'w') as fd:
      fd.write(data)
    os.chmod(outfile, os.stat(outfile).st_mode | 0555)

  def _WriteUpdateScript(self, image_files, script, stable_main_version,
                         stable_ec_version, stable_pd_version):
    """Create and write the update script which will run on the device.

    This generates the beginnings of the output file (shellball) based on the
    template. Normally all the required settings for firmware update are at the
    top of the script. With unified builds only TARGET_SCRIPT is used: the
    rest are unset and the model-specific setvars.sh files contain the rest
    of the settings.

    Args:
      image_files: Dict with:
          key: Image type (e.g. 'BIOS').
          value: ImageFile object containing filename and version.
      script: Filename of update script being used (e.g. 'updater4.sh').
      stable_main_version: Version name of stable main firmware, or None.
      stable_ec_version: Version name of stable EC firmware, or None.
      stable_pd_version: Version name of stable PD firmware, or None.
    """
    empty = ImageFile(None, '')
    bios_rw_version = image_files.get('BIOS (RW)', empty).version
    if not bios_rw_version:
      bios_rw_version = image_files['BIOS'].version
    full_dict = {
        'REPLACE_RO_FWID': image_files['BIOS'].version,
        'REPLACE_FWID': bios_rw_version,
        'REPLACE_ECID': image_files.get('EC', empty).version or IGNORE,
        'REPLACE_PDID': image_files.get('PD', empty).version or IGNORE,
        # Set platform to first field of firmware version
        # (ex: Google_Link.1234 -> Google_Link).
        'REPLACE_PLATFORM': image_files['BIOS'].version.split('.')[0],
        'REPLACE_STABLE_FWID': stable_main_version,
        'REPLACE_STABLE_ECID': stable_ec_version,
        'REPLACE_STABLE_PDID': stable_pd_version,
    }
    replace_dict = dict(full_dict)
    replace_dict['REPLACE_SCRIPT'] = script
    self._CreateFileFromTemplate(self._stub_file, self._args.output,
                                 replace_dict)
    return full_dict

  def _WriteVersionFile(self):
    """Write out the VERSION file with our collected version information."""
    print(file=self._versions)
    with open(self._BaseDirPath('VERSION'), 'w') as fd:
      fd.write(self._versions.getvalue())

  def _BuildShellball(self):
    """Build a shell-ball containing the firmware update.

    Add our files to the shell-ball, and display all version information.
    """
    cros_build_lib.RunCommand(
        ['sh', self._args.output, '--sb_repack', self._basedir],
        mute_output=False)
    if not self._args.quiet:
      for fname in glob.glob(self._BaseDirPath('VERSION*')):
        with open(fname) as fd:
          print(fd.read())

  def _ProcessModel(self, model, bios_image, bios_rw_image, ec_image, pd_image,
                    default_ec_version, create_bios_rw_image, tools, tool_base,
                    script, extras):
    """Set up the firmware update for one model.

    Args:
      model: name of model to set up for (e.g. 'reef'). If this is not a
          unified build then this should be ''.
      bios_image: Input filename of main BIOS (main) image.
      bios_rw_image: Input filename of RW BIOS image, or None if none.
      ec_image: Input filename of EC (Embedded Controller) image.
      pd_image: Input filename of PD (Power Delivery) image.
      default_ec_version: Default EC version string to use if no version
          can be detected in ec_image.
      create_bios_rw_image: True to create a RW BIOS image from the main one
          if not provided.
      tools: List of tools to include in the update (e.g. ['mosys', 'ectool'].
      tool_base: List of directories to look in for tools.
      script: Update script to use (e.g. 'updater4.sh').
      extras: List of extra files/directories to include.

    Returns:
      Dict containing information on the output image files:
        key: Name, one of "BIOS", "BIOS (RW)", "EC", "EC (RW)", "PD", "PD (RW)".
        value: ImageFile, containing filename and version.
    """
    main_script = os.path.join(self._pack_dist, script)
    if not os.path.exists(main_script):
      raise PackError("Cannot find required file '%s'" % main_script)
    for tool in tools:
      self._FindTool(tool_base, tool)
    if not bios_image and not ec_image and not pd_image:
      raise PackError('Must assign at least one of BIOS or EC or PD image')
    image_files = self._CopyFirmwareFiles(
        bios_image=bios_image,
        bios_rw_image=bios_rw_image,
        ec_image=ec_image,
        pd_image=pd_image,
        default_ec_version=default_ec_version,
        create_bios_rw_image=create_bios_rw_image,
        image_main=os.path.join(self._basedir, model, IMAGE_MAIN),
        image_main_rw=os.path.join(self._basedir, model, IMAGE_MAIN_RW),
        image_ec=os.path.join(self._basedir, model, IMAGE_EC),
        image_pd=os.path.join(self._basedir, model, IMAGE_PD))
    self._WriteVersions(image_files)
    self._CopyBaseFiles(tool_base, tools, script)
    if extras:
      self._CopyExtraFiles(extras)
    return image_files

  def Start(self, argv, remove_tmpdirs=True):
    """Handle the creation of a firmware shell-ball.

    argv: List of arguments (excluding the program name/argv[0]).

    Raises:
      PackError if any error occurs.
    """
    args = self._args = self.ParseArgs(argv)

    self._EnsureCommand('shar', 'sharutils')
    if not os.path.exists(self._stub_file):
      raise PackError("Cannot find required file '%s'" % self._stub_file)
    tool_base = args.tool_base.split(':')
    try:
      if not args.output:
        raise PackError('Missing output file')
      self._basedir = self._CreateTmpDir()
      self._tmpdir = self._CreateTmpDir()
      self._AddFlashromVersion(tool_base)
      image_files = self._ProcessModel(
          model='',
          bios_image=args.bios_image,
          bios_rw_image=args.bios_rw_image,
          ec_image=args.ec_image,
          pd_image=args.pd_image,
          default_ec_version=args.ec_version,
          create_bios_rw_image=args.create_bios_rw_image,
          tools=args.tools.split(),
          tool_base=tool_base,
          script=args.script,
          extras=args.extra.split(':') if args.extra else [])
      self._WriteUpdateScript(
          image_files=image_files,
          script=args.script,
          stable_main_version=args.stable_main_version,
          stable_ec_version=args.stable_ec_version,
          stable_pd_version=args.stable_pd_version)
      self._WriteVersionFile()
      self._BuildShellball()
      if not args.quiet:
        print('Packed output image is: %s' % args.output)
    finally:
      if remove_tmpdirs:
        self._RemoveTmpdirs()


# The style guide says that we cannot pass in sys.argv[0]. That makes testing
# a pain, so this is a full argv.
def main(argv):
  global packer

  packer = FirmwarePacker(argv[0])
  packer.Start(argv[1:])

if __name__ == "__main__":
  main(sys.argv)
