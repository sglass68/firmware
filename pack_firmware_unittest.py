# Copyright 2017 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for pack_firmware.py.

This mocks out all tools so it can run fairly quickly.
"""

from __future__ import print_function

from contextlib import contextmanager
import glob
import mock
import os
import shutil
import sys
import unittest

try:
  from StringIO import StringIO
except ImportError:
  from io import StringIO

from chromite.lib import cros_build_lib_unittest
from chromite.lib import partial_mock
import chromite.lib.cros_logging as logging
import pack_firmware

# Disable all logging as it's confusing to get log output from tests.
logging.getLogger().setLevel(logging.CRITICAL + 1)

# Pre-set ID expected for test/image.bin. Note the 'R' in the first is a 'W'
# in the second. It is confusing but this is how the firmware images are
# currently created.
RO_FRID = 'Google_Reef.9264.0.2017_02_09_1240'
RW_FWID = 'Google_Reef.9264.0.2017_02_09_1250'

# Expected output from vbutil.
VBUTIL_OUTPUT = '''Key block:
  Size:                2232
  Flags:               7 (ignored)
  Data key algorithm:  7 RSA4096 SHA256
  Data key version:    1
  Data key sha1sum:    e2c1c92d7d7aa7dfed5e8375edd30b7ae52b7450
Preamble:
  Size:                  2164
  Header version:        2.1
  Firmware version:      1
  Kernel key algorithm:  7 RSA4096 SHA256
  Kernel key version:    1
  Kernel key sha1sum:    5d2b220899c4403d564092ada3f12d3cc4483223
  Firmware body size:    920000
  Preamble flags:        1
Body verification succeeded.
'''

# Expected output from 'dump_fmap -p' for main image.
FMAP_OUTPUT = '''WP_RO 0 4194304
SI_DESC 0 4096
IFWI 4096 2093056
RO_VPD 2097152 16384
RO_SECTION 2113536 2080768
FMAP 2113536 2048
RO_FRID 2115584 64
RO_FRID_PAD 2115648 1984
COREBOOT 2117632 1552384
GBB 3670016 262144
RO_UNUSED 3932160 262144
MISC_RW 4194304 196608
UNIFIED_MRC_CACHE 4194304 135168
RECOVERY_MRC_CACHE 4194304 65536
RW_MRC_CACHE 4259840 65536
RW_VAR_MRC_CACHE 4325376 4096
RW_ELOG 4329472 12288
RW_SHARED 4341760 16384
SHARED_DATA 4341760 8192
VBLOCK_DEV 4349952 8192
RW_VPD 4358144 8192
RW_NVRAM 4366336 24576
RW_SECTION_A 4390912 4718592
VBLOCK_A 4390912 65536
FW_MAIN_A 4456448 4652992
RW_FWID_A 9109440 64
RW_SECTION_B 9109504 4718592
VBLOCK_B 9109504 65536
FW_MAIN_B 9175040 4652992
RW_FWID_B 13828032 64
RW_LEGACY 13828096 2097152
BIOS_UNUSABLE 15925248 323584
DEVICE_EXTENSION 16248832 524288
UNUSED_HOLE 16773120 4096
'''

# Size of dummy 'ecrw' file.
ECRW_SIZE = 0x38000

# Expected output from 'dump_fmap -p' for EC image.
FMAP_OUTPUT_EC = '''EC_RO 64 229376
FR_MAIN 64 229376
RO_FRID 388 32
FMAP 135232 350
WP_RO 0 262144
EC_RW 262144 229376
RW_FWID 262468 32
'''

# Common flags that we use in several tests.
COMMON_FLAGS = [
  '--script=updater5.sh', '--tools', 'flashrom dump_fmap',
  '--tool_base', 'test', '-b', 'test/image.bin', '-q',  '-o' 'out',
]


# Use this to suppress stdout/stderr output:
# with capture_sys_output() as (stdout, stderr)
#   ...do something...
@contextmanager
def capture_sys_output():
  capture_out, capture_err = StringIO(), StringIO()
  old_out, old_err = sys.stdout, sys.stderr
  try:
    sys.stdout, sys.stderr = capture_out, capture_err
    yield capture_out, capture_err
  finally:
    sys.stdout, sys.stderr = old_out, old_err


class TestUnit(unittest.TestCase):
  """Test cases for common program flows."""
  def setUp(self):
    self.packer = pack_firmware.FirmwarePacker('.')
    # Limit the resolution of timestamps to aid comparison.
    os.stat_float_times(False)
    self._ExtractFrid = pack_firmware.FirmwarePacker._ExtractFrid

  def tearDown(self):
    pack_firmware.FirmwarePacker._ExtractFrid = self._ExtractFrid

  def testBadStartup(self):
    """Test various bad start-up conditions"""
    # Starting up in another directory (without required files) should fail.
    with self.assertRaises(pack_firmware.PackError) as e:
      pack_firmware.main(['/'])
    self.assertIn("'/pack_stub'", str(e.exception))

    # Comment
    with self.assertRaises(pack_firmware.PackError) as e:
      pack_firmware.main(['test', '-o', 'out', '--tool_base', 'test'])
    self.assertIn("'pack_dist/updater.sh'", str(e.exception))

    # Should check for 'shar' tool.
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('type shar'), returncode=1)
      with self.assertRaises(pack_firmware.PackError) as e:
        pack_firmware.main(['.'])
      self.assertIn("'shar'", str(e.exception))

    # Should complain about missing tools.
    with self.assertRaises(pack_firmware.PackError) as e:
      pack_firmware.main(['.', '--script=updater5.sh', '--tool_base', 'test',
                          '--tools', 'missing-tool', '-o', 'out'])
    self.assertIn("'missing-tool'", str(e.exception))

    # Should complain if we don't provide at least one image.
    with self.assertRaises(pack_firmware.PackError) as e:
      args = ['.', '--script=updater5.sh', '--tools', 'ls',
              '--tool_base', '/bin:test', '-o', 'out']
      pack_firmware.main(args)
    self.assertIn('Must assign at least one', str(e.exception))

  def testArgParse(self):
    """Test some basic argument parsing as a sanity check."""
    with self.assertRaises(SystemExit):
      with capture_sys_output():
        self.assertEqual(None, self.packer.ParseArgs(['--invalid']))

    self.assertEqual(None, self.packer.ParseArgs([]).bios_image)
    self.assertEqual('bios.bin',
                     self.packer.ParseArgs(['-b', 'bios.bin']).bios_image)

    self.assertEqual(True, self.packer.ParseArgs([]).remove_inactive_updaters)
    self.assertEqual(False,
                     self.packer.ParseArgs(['--no-remove_inactive_updaters'])
                     .remove_inactive_updaters)

    self.assertEqual(True, self.packer.ParseArgs([]).merge_bios_rw_image)
    self.assertEqual(True, self.packer.ParseArgs(['--merge_bios_rw_image'])
                     .merge_bios_rw_image)
    self.assertEqual(False, self.packer.ParseArgs(['--no-merge_bios_rw_image'])
                     .merge_bios_rw_image)

  def testEnsureCommand(self):
    """Check that we detect a missing command."""
    self.packer._EnsureCommand('ls', 'sample-package')
    with self.assertRaises(pack_firmware.PackError) as e:
      self.packer._EnsureCommand('does-not-exist', 'sample-package')
    self.assertIn("You need 'does-not-exist'", str(e.exception))

  def testFindTool(self):
    """Check finding of required tools."""
    self.packer._args = self.packer.ParseArgs(['--tool_base', 'test'])
    with self.assertRaises(pack_firmware.PackError) as e:
      self.packer._FindTool(['test'], 'does-not-exist')
    self.assertIn("'does-not-exist'", str(e.exception))
    self.packer._FindTool(['test'], 'flashrom')

  def testTmpdirs(self):
    """Check creation and removal of temporary directories."""
    dir1 = self.packer._CreateTmpDir()
    dir2 = self.packer._CreateTmpDir()
    self.assertTrue(os.path.exists(dir1))
    self.assertTrue(os.path.exists(dir2))
    self.packer._RemoveTmpdirs()
    self.assertFalse(os.path.exists(dir1))
    self.assertFalse(os.path.exists(dir2))

  def testAddFlashromVersion(self):
    """Test we can add the flashrom version to the version information."""
    self.packer._args = self.packer.ParseArgs(['--tool_base', 'test'])
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('file'), returncode=0,
                      output='ELF 64-bit LSB executable, etc.\n')
      self.packer._AddFlashromVersion(self.packer._args.tool_base.split(':'))
    result = self.packer._versions.getvalue().splitlines()
    self.assertIn('flashrom(8)', result[1])
    self.assertIn('ELF 64-bit LSB executable', result[2])
    self.assertEqual('%s0.9.4  : 1bb61e1 : Feb 07 2017 18:29:17 UTC' %
                     (' ' * 13), result[3])

  def testAddFlashromMissingVersion(self):
    """Test we can add the local flashrom version to the version information.

    A local flashrom is built with cros_workon enabled for the flashrom ebuild.
    It does not include the git hash or date, but is still considered to be
    valid.
    """
    self.packer._args = self.packer.ParseArgs(['--tool_base', 'local_flashrom'])
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('file'), returncode=0,
                      output='ELF 64-bit LSB executable, etc.\n')
      self.packer._AddFlashromVersion(self.packer._args.tool_base.split(':'))
    result = self.packer._versions.getvalue().splitlines()
    self.assertIn('flashrom(8)', result[1])
    self.assertIn('ELF 64-bit LSB executable', result[2])
    self.assertEqual(' ' * 13, result[3])

  def testAddVersionInfoMissingFile(self):
    """Trying to add version info for a missing file should be detected."""
    with self.assertRaises(IOError) as e:
      self.packer._AddVersionInfo('BIOS', 'missing-file', 'v123')
    self.assertIn("'missing-file'", str(e.exception))

  def testAddVersionInfoNoFile(self):
    """Check adding version info with no filename."""
    self.packer._AddVersionInfo('BIOS', '', 'v123')
    self.assertEqual('BIOS version: v123\n', self.packer._versions.getvalue())

  def testAddVersionNoVersion(self):
    """Check adding version info with no version."""
    self.packer._AddVersionInfo('BIOS', 'test/image.bin', '')
    self.assertEqual('BIOS image:   8ce05b02847603aef6cfa01f1bab73d0 '
                     '*test/image.bin\n',
                     self.packer._versions.getvalue())

  def testAddVersionInfo(self):
    """Check adding version info with both a filename and version."""
    self.packer._AddVersionInfo('BIOS', 'test/image.bin', 'v123')
    self.assertEqual('BIOS image:   8ce05b02847603aef6cfa01f1bab73d0 '
                     '*test/image.bin\nBIOS version: v123\n',
                     self.packer._versions.getvalue())

  def testExtractFrid(self):
    """Check extracting the firmware ID from a bios image."""
    self.packer._tmpdir = 'test'
    self.packer._testing = True
    self.packer._args = self.packer.ParseArgs(['--bios_image', 'image.bin'])
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('dump_fmap'), returncode=0)
      self.assertEqual(RO_FRID, self.packer._ExtractFrid('image.bin'))

  def testExtractFridTrailingSpace(self):
    """Check extracting a firmware ID with a trailing space."""
    def _SetupImage(_, **kwargs):
      destdir = kwargs['cwd']
      with open(os.path.join(destdir, 'RO_FRID'), 'wb') as fd:
        fd.write('TESTING \0\0\0')

    self.packer._tmpdir = self.packer._CreateTmpDir()
    self.packer._testing = True
    self.packer._args = self.packer.ParseArgs(['--bios_image', 'image.bin'])
    with cros_build_lib_unittest.RunCommandMock() as rc:
      rc.AddCmdResult(partial_mock.ListRegex('dump_fmap'), returncode=0,
                      side_effect=_SetupImage)
      self.assertEqual('TESTING', self.packer._ExtractFrid('image.bin'))
    self.packer._RemoveTmpdirs()

  def testUntarFile(self):
    """Test operation of the tar file unpacker."""
    dirname = self.packer._CreateTmpDir()
    fname = self.packer._UntarFile('functest/Reef.9042.50.0.tbz2', dirname)
    self.assertEqual(os.path.basename(fname), 'image.bin')

    # This tar file has two files in it.
    # -rw-r----- sjg/eng          64 2017-03-03 16:12 RO_FRID
    # -rw-r----- sjg/eng          64 2017-03-15 13:38 RW_FRID
    with self.assertRaises(pack_firmware.PackError) as e:
      fname = self.packer._UntarFile('test/two_files.tbz2', dirname)
    self.assertIn('Expected 1 member', str(e.exception))

    # This tar file has as directory name in its member's filename.
    # -rw-r----- sjg/eng          64 2017-03-03 16:12 test/RO_FRID
    with self.assertRaises(pack_firmware.PackError) as e:
      fname = self.packer._UntarFile('test/path.tbz2', dirname)
    self.assertIn('should be a simple name', str(e.exception))

    self.packer._RemoveTmpdirs()

  def _FilesInDir(self, dirname):
    """Get a list of files in a directory.

    Args:
      dirname: Directory name to check.

    Returns:
      List of files in that directory (basename only). Any subdirectories are
          ignored.
    """
    return sorted([os.path.basename(fname)
                   for fname in glob.glob(os.path.join(dirname, '*'))
                   if not os.path.isdir(fname)])

  def testCopyExtraFiles(self):
    """Test that we can copy 'extra' files."""
    self.packer._basedir = dirname = self.packer._CreateTmpDir()
    self.packer._CopyExtraFiles(['test/RO_FRID', 'test/RW_FWID'])
    self.assertEqual(self._FilesInDir(dirname), ['RO_FRID', 'RW_FWID'])
    self.packer._RemoveTmpdirs()

  def testCopyExtraDir(self):
    """Test that we can copy 'extra' directories."""
    self.packer._basedir = dirname = self.packer._CreateTmpDir()
    self.packer._CopyExtraFiles(['test'])
    self.assertEqual(self._FilesInDir(dirname), self._FilesInDir('test'))
    self.packer._RemoveTmpdirs()

  def testCopyExtraTarfile(self):
    """Test that we can extract 'extra' tarfile contents."""
    self.packer._basedir = dirname = self.packer._CreateTmpDir()
    self.packer._args = self.packer.ParseArgs(['--imagedir', 'functest'])

    # The bcs:// prefix tells us that the file was downloaded from BCS, and
    # that we should unpack its contents. This file contains 'image.bin'.
    self.packer._CopyExtraFiles(['bcs://Reef.9042.50.0.tbz2'])
    self.assertEqual(self._FilesInDir(dirname), ['image.bin'])
    self.packer._RemoveTmpdirs()

  def testBaseDirPath(self):
    """Check that _BaseDirPath() works as expected."""
    self.packer._basedir = 'base'
    self.assertEqual('base/fred', self.packer._BaseDirPath('fred'))

  def _SetUpConfigNode(self):
    """Set up ready our test configuration ready for use.

    Returns:
      Integer offset of the firmware node.
    """
    self.packer._SetUpConfig('test/config.dtb')
    return self.packer._config.path_offset('/chromeos/models/reef/firmware')

  def testGetString(self):
    """Check that we can read strings from the master configuration."""
    node = self._SetUpConfigNode()
    self.assertEqual(self.packer._GetString(node, 'bcs-overlay'),
                     'overlay-reef-private')
    self.assertEqual(self.packer._GetString(node, 'missing'), '')

  def testGetStringList(self):
    """Check that we can read string lists from the master configuration."""
    node = self._SetUpConfigNode()
    self.assertEqual(self.packer._GetStringList(node, 'extra'),
                     ['${FILESDIR}/extra', '${SYSROOT}/usr/sbin/ectool',
                      'bcs://Reef.9000.0.0.tbz2'])
    self.assertEqual(self.packer._GetStringList(node, 'missing'), [])

  def testGetBool(self):
    """Check that we can read booleans from the master configuration."""
    node = self._SetUpConfigNode()
    self.assertEqual(self.packer._GetBool(node, 'script'), True)
    self.assertEqual(self.packer._GetBool(node, 'missing'), False)

  def testExtractFileLocal(self):
    """Test handling file extraction based on a configuration property,"""
    node = self._SetUpConfigNode()
    self.packer._args = self.packer.ParseArgs(['-l'])
    # Since the 'extra' property exists, this should give as a valid file.
    self.assertEqual(
        self.packer._ExtractFile('reef', 'test/MODEL-files/MODEL_fake_extra',
                                 node, 'extra', None),
        'test/reef-files/reef_fake_extra')

    # Since 'missing-prop' does not exist, this should return None.
    self.assertEqual(
        self.packer._ExtractFile('reef', 'test/MODEL-files/MODEL_fake_extra',
                                 node, 'missing-prop', None),
        None)

  def testExtractFileBcs(self):
    """Test handling file extraction based on a configuration property,"""
    self.packer._args = self.packer.ParseArgs(['--imagedir', 'functest'])
    dirname = self.packer._CreateTmpDir()
    node = self._SetUpConfigNode()

    # This should look up main-image, find that file in functest/, copy it to
    # our directory and return its filename.
    self.assertEqual(
        self.packer._ExtractFile(None, None, node, 'main-image', dirname),
        os.path.join(dirname, 'image.bin'))
    self.assertEqual(
        self.packer._ExtractFile(None, None, node, 'ec-image', dirname),
        os.path.join(dirname, 'ec.bin'))
    self.packer._RemoveTmpdirs()

  def _AddMocks(self, rc):
    def _CopySections(_, **kwargs):
      destdir = kwargs['cwd']
      for fname in ['RO_FRID', 'RW_FWID']:
        shutil.copy2(os.path.join('test', fname), destdir)

    rc.AddCmdResult(partial_mock.Regex('type shar'), returncode=0)
    rc.AddCmdResult(partial_mock.ListRegex('file'), returncode=0,
                    output='ELF 64-bit LSB executable, etc.\n')
    rc.AddCmdResult(partial_mock.ListRegex('dump_fmap -x .*image.bin'),
                    side_effect=_CopySections, returncode=0)
    rc.AddCmdResult(partial_mock.ListRegex('gbb_utility'), returncode=0,
                    output=' - exported root_key to file: rootkey.bin')
    rc.AddCmdResult(partial_mock.ListRegex('vbutil_firmware'), returncode=0,
                    output=VBUTIL_OUTPUT)
    rc.AddCmdResult(partial_mock.ListRegex('dump_fmap -x .*bios_rw.bin'),
                    side_effect=_CopySections, returncode=0)
    rc.AddCmdResult(partial_mock.ListRegex('--sb_repack'), returncode=0)
    rc.AddCmdResult(partial_mock.ListRegex('dump_fmap -x .*ec.bin'),
                    side_effect=_CopySections, returncode=0)
    rc.AddCmdResult(partial_mock.ListRegex('dump_fmap -x .*pd.bin'),
                    side_effect=_CopySections, returncode=0)

  @classmethod
  def _ResignFirmware(self, cmd, **_):
    """Called as a side effect to emulate the effect of resign_firmwarefd.sh.

    This copies the input file to the output file.

    Args:
      cmd: Arguments, of the form:
          ['resign_firmwarefd.sh', <infile>, <outfile>, ...]
          See _SetPreambleFlags() for where this command is generated.
    """
    infile, outfile = cmd[1], cmd[2]
    shutil.copy(infile, outfile)

  def _MockGetPreambleFlags(self, fname, **_):
    """Mock of _GetPreambleFlags(). Uses the filename to determine value.

    Args:
      fname: Image filename to check.

    Returns:
      0 if the image appears to be an RW image, 1 if not.
    """
    return 0 if 'rw' in fname else 1

  def testMockedRun(self):
    """Start up with a valid updater script and BIOS."""
    pack_firmware.FirmwarePacker._GetPreambleFlags = (
        mock.Mock(side_effect=self._MockGetPreambleFlags))
    args = ['.', '--create_bios_rw_image', '-e', 'test/ec.bin'] + COMMON_FLAGS
    with cros_build_lib_unittest.RunCommandMock() as rc:
      self._AddMocks(rc)
      rc.AddCmdResult(partial_mock.ListRegex('resign_firmwarefd.sh'),
                      side_effect=self._ResignFirmware, returncode=0)
      pack_firmware.main(args)
      pack_firmware.packer._versions.getvalue().splitlines()

  def testMockedRunWithMerge(self):
    """Start up with a valid updater script and merge the RW BIOS."""
    def _CreateFile(cmd, **_):
      """Called as a side effect to emulate the effect of cbfstool.

      This handles the 'cbfstool...extract' command which is supposed to
      extract a particular 'file' from inside the CBFS archive. We deal with
      this by creating a zero-filled file with the correct name and size.
      See _ExtractEcRwUsingCbfs() for where this command is generated.

      Args:
        cmd: Arguments, of the form:
            ['cbfstool.sh', ..., '-f', <filename>, ...]
            See _SetPreambleFlags() for where this is generated.
      """
      file_arg = cmd.index('-f')
      fname = cmd[file_arg + 1]
      with open(fname, 'wb') as fd:
        fd.seek(ECRW_SIZE - 1)
        fd.write('\0')

    pack_firmware.FirmwarePacker._GetPreambleFlags = (
        mock.Mock(side_effect=self._MockGetPreambleFlags))
    args = ['--bios_rw_image', 'test/image_rw.bin',
            '--merge_bios_rw_image', '-e', 'test/ec.bin', '-p', 'test/pd.bin',
            '--remove_inactive_updaters'] + COMMON_FLAGS
    with cros_build_lib_unittest.RunCommandMock() as rc:
      self._AddMocks(rc)
      rc.AddCmdResult(partial_mock.ListRegex(
          'dump_fmap -x .*test/image_rw.bin'), returncode=0)
      rc.AddCmdResult(partial_mock.ListRegex('dump_fmap -p test/image_rw.bin'),
                      returncode=0, output=FMAP_OUTPUT)
      rc.AddCmdResult(partial_mock.ListRegex('dump_fmap -p .*bios.bin'),
                      returncode=0, output=FMAP_OUTPUT)
      rc.AddCmdResult(partial_mock.Regex('extract_ecrw'), returncode=0)
      rc.AddCmdResult(partial_mock.ListRegex('dump_fmap -p .*ec.bin'),
                      returncode=0, output=FMAP_OUTPUT_EC)
      rc.AddCmdResult(partial_mock.ListRegex('cbfstool'), returncode=0,
                      side_effect=_CreateFile)
      rc.AddCmdResult(partial_mock.ListRegex('dump_fmap -p .*pd.bin'),
                      returncode=0, output=FMAP_OUTPUT_EC)
      self.packer.Start(args, remove_tmpdirs=False)
    result = self.packer._versions.getvalue().splitlines()
    self.assertEqual(15, len(result))
    self.assertIn(RO_FRID, self._FindLineInList(result, 'EC version'))
    self.assertIn(RW_FWID, self._FindLineInList(result, 'EC (RW) version'))
    rw_fname = self.packer._BaseDirPath(pack_firmware.IMAGE_EC)
    self.assertEqual(os.stat(rw_fname).st_mtime,
                     os.stat('test/image_rw.bin').st_mtime)
    ec_fname = self.packer._BaseDirPath(pack_firmware.IMAGE_EC)
    self.assertEqual(os.stat(ec_fname).st_mtime,
                     os.stat('test/image_rw.bin').st_mtime)
    self.packer._RemoveTmpdirs()

  def testMockedRunUnibuildBad(self):
    """Try unified build options with invalid arguments."""

    args = ['.', '-m', 'reef', '-o', 'output', '--tool_base', 'test']
    with self.assertRaises(pack_firmware.PackError) as e:
      pack_firmware.main(args)
    self.assertIn("Missing master configuration file", str(e.exception))

  def testMockedRunWithUnibuild(self):
    """Start up with a valid updater script and BIOS."""

    args = ['.', '-m', 'reef', '-m', 'pyro', '-q', '-o' 'out',
            '-c', 'test/config.dtb', '--tool_base', 'test',
            '--tools', 'flashrom dump_fmap', '-i', 'functest']
    os.environ['SYSROOT'] = 'test'
    os.environ['FILESDIR'] = 'test'
    with cros_build_lib_unittest.RunCommandMock() as rc:
      self._AddMocks(rc)
      pack_firmware.main(args)
      pack_firmware.packer._versions.getvalue().splitlines()

  def _FindLineInList(self, lines, start_text):
    """Helper to find a single line starting with the given text and return it.

    Args:
      lines: List of lines to check.
      text: Text to find.

    Returns:
      Line found, as a string (or assertion failure if exactly one matching
        line was not found).
    """
    found = [line for line in lines if line.strip().startswith(start_text)]
    self.assertEqual(len(found), 1)
    return found[0]

  def testRWFirmware(self):
    """Simple test of creating RW firmware."""
    pack_firmware.FirmwarePacker._GetPreambleFlags = (
        mock.Mock(side_effect=self._MockGetPreambleFlags))
    args = ['--create_bios_rw_image', '-e', 'test/ec.bin'] + COMMON_FLAGS
    with cros_build_lib_unittest.RunCommandMock() as rc:
      self._AddMocks(rc)
      rc.AddCmdResult(partial_mock.ListRegex('resign_firmwarefd.sh'),
                      side_effect=self._ResignFirmware, returncode=0)
      self.packer.Start(args, remove_tmpdirs=False)
    rw_fname = self.packer._BaseDirPath(pack_firmware.IMAGE_MAIN_RW)
    self.assertEqual(os.stat('test/image.bin').st_mtime,
                     os.stat(rw_fname).st_mtime)
    self.packer._RemoveTmpdirs()

    # This VERSION file should contain 12 lines of output:
    # 1 blank line
    # 3 for flashrom
    # 1 blank line
    # 2 for RO BIOS filename and version
    # 2 for RW BIOS filename and version
    # 2 for EC filename and version
    # 1 blank line
    result = self.packer._versions.getvalue().splitlines()
    self.assertEqual(12, len(result))
    rw_line = self._FindLineInList(result, 'BIOS (RW) image')
    self.assertIn(pack_firmware.IMAGE_MAIN_RW, rw_line)
    self.assertNotIn(self.packer._basedir, rw_line)

  def testNoECFirmware(self):
    """Simple test of creating firmware without an EC image."""
    args = COMMON_FLAGS
    with cros_build_lib_unittest.RunCommandMock() as rc:
      self._AddMocks(rc)
      self.packer.Start(args)

    # There should be no EC version in the VERSION file.
    result = self.packer._versions.getvalue()
    self.assertNotIn('EC version', result)
    self.assertEqual(8, len(result.splitlines()))

    # In the script, the EC version should be 'IGNORE'.
    with open('out') as fd:
      lines = fd.read().splitlines()
    self.assertIn('IGNORE', self._FindLineInList(lines, 'TARGET_ECID'))

  # b/36104199 Should be able to be removed once mario is turned down.
  def testMario(self):
    """Test the special cases required by mario."""
    def _MockExtractFrid(image_file):
      return ''

    pack_firmware.FirmwarePacker._GetPreambleFlags = (
        mock.Mock(side_effect=self._MockGetPreambleFlags))
    pack_firmware.FirmwarePacker._ExtractFrid = (
        mock.Mock(side_effect=_MockExtractFrid))

    args = COMMON_FLAGS
    with cros_build_lib_unittest.RunCommandMock() as rc:
      self._AddMocks(rc)
      self.packer.Start(args)

    # There should be a BIOS image but no version in the VERSION file.
    result = self.packer._versions.getvalue()
    result_lines = result.splitlines()
    self.assertIn('test/image.bin', self._FindLineInList(result_lines, 'BIOS'))
    self.assertNotIn('BIOS version', result)
    self.assertEqual(7, len(result.splitlines()))

    # In the script, the versions should be 'IGNORE'.
    with open('out') as fd:
      lines = fd.read().splitlines()
    for item in 'RO_FWID FWID ECID PDID PLATFORM'.split():
      self.assertIn('IGNORE', self._FindLineInList(lines, 'TARGET_%s' % item))


if __name__ == '__main__':
  unittest.main()
