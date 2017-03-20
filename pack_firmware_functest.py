# Copyright 2017 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
"""Functional test for pack_firmware.py.

This runs a basic scenario and checks the output by running the update script
with a few fake tools.
"""

from __future__ import print_function

import os
import re
import shutil
import tarfile
import tempfile
import unittest

from chromite.lib import cros_build_lib

from pack_firmware import FirmwarePacker

REEF_HWID = 'Reef A12-B3C-D5E-F6G-H7I'
REEF_STABLE_MAIN_VERSION = 'Google_Reef.9042.43.0'
UPDATER = 'updater4.sh'

# We are looking for KEY="VALUE", or KEY=
RE_KEY_VALUE = re.compile('(?P<key>[A-Z_]+)=("(?P<value>.*)")?$')

# Firmware update runs on the device using the dash shell. Try to use this if
# available.
HAVE_DASH = os.path.exists('/bin/dash')
SHELL = '/bin/dash' if HAVE_DASH else '/bin/sh'


class TestFunctional(unittest.TestCase):
  """Functional test for firmware packer script.

  Members:
    indir: Directory which contains the input firmware files (e,g. image.bin).
    basedir: Directory containing this script.
    outdir: Directory to place output shellball.
    unpackdir: Directory used to unpack shellball into.
  """

  def setUp(self):
    self.packer = FirmwarePacker('test')
    tmp_base = 'pack_firmwaretest-%d' % os.getpid()
    self.indir = tempfile.mkdtemp(tmp_base)
    with tarfile.open('functest/Reef.9042.50.0.tbz2') as tar:
      tar.extractall(self.indir)
    self.basedir = os.path.realpath(os.path.dirname(__file__))
    if os.path.exists('/etc/cros_chroot_version'):
      self.chroot = '/'
    else:
      self.chroot = os.path.join(self.basedir, '../../../chroot')
    self.outdir = tempfile.mkdtemp(tmp_base)
    self.unpackdir = tempfile.mkdtemp(tmp_base)
    self.packer._force_dash = HAVE_DASH

  def _ExpectedFiles(self, extra_files):
    """Get a sorted list of files that we expect to see in the shellball.

    Args:
      extra_files: A list of extra files to include.

    Returns:
      A sorted list of files to expect.
    """
    expected_files = (
        'crosfw.sh crosutil.sh flashrom mosys VERSION.md5 common.sh crossystem '
        'dump_fmap gbb_utility shflags VERSION vpd').split(' ')
    expected_files.append(UPDATER)
    if extra_files:
      expected_files += extra_files
    return sorted(expected_files)

  def _RunScript(self, outfile, hwid, stable_main_version):
    """Run an autoupdate with the shellball and check that it works.

    This relies on fake tools, principally crossystem which is controlled by
    environment variables set here.

    Args:
      outfile: Shellball output file to test.
      hwid: Hardware ID to provide when the script asks for the hardware ID
      stable_main_version: Value to return when RO_FWID is requested.
    """
    # These are used by our fake crossystem script (see functest/ directory).
    os.environ['FAKE_BIOS_BIN'] = os.path.join(self.indir, 'image.bin')
    os.environ['FAKE_FWID'] = stable_main_version
    os.environ['FAKE_FW_VBOOT2'] = '0'
    os.environ['FAKE_HWID'] = hwid
    os.environ['FAKE_MAINFW_ACT'] = 'A'
    os.environ['FAKE_RO_FWID'] = stable_main_version
    os.environ['FAKE_TPM_FWVER'] = '1'
    os.environ['FAKE_WPSW_CUR'] = '1'    # RO firmware is write-protected.
    os.environ['FAKE_VDAT_FLAGS'] = '0'  # Not using RO normal.
    result = cros_build_lib.RunCommand(
        [SHELL, outfile, '--mode', 'autoupdate', '--verbose', '--debug'],
        capture_output=True)

    # We expect debugging output but should not get anything else.
    errors = [line for line in result.error.splitlines()
              if not line.startswith(' (DEBUG')]
    self.assertEqual(errors, [])

  def _ReadVersions(self, fname):
    """Read the start of the supplied script to get the version information.

    This picks up various shell variable assignments from the script and
    returns them so their values can be checked.

    Args:
      fname: Filename of script file.

    Returns:
      Dict with:
         key: Shell variable.
         value: Value of that shell variable.
    """
    with open(fname) as fd:
      lines = fd.read(1000).splitlines()[:30]
    # Use strip() where needed since some lines are indented.
    lines = [line.strip() for line in lines
             if line.strip().startswith('TARGET') or
             line.strip().startswith('STABLE') or
             line.startswith('UNIBUILD')]
    versions = {}
    for line in lines:
      m = RE_KEY_VALUE.match(line)
      value = m.group('value')
      versions[m.group('key')] = value if value else ''

    return versions

  def _RunPackFirmware(self, extra_args):
    """Run the FirmwarePacker process and read the resulting shellball.

    Args:
      extra_args: Extra arguments to pass to FirmwarePacker.

    Returns:
      Tuple containing:
        Path to output shellball.
        Sorted list of files in the shellball.
        Dict containing the version information, with each entry being:
          key: shell variable (e.g. TARGET_FWID).
          value: value of that variable.
    """
    tool_path = [
        os.path.join(self.basedir, 'functest'),
        os.path.join(self.chroot, 'usr/sbin'),
        os.path.join(self.chroot, 'usr/bin'),
    ]
    outfile = os.path.join(self.outdir, 'output.sh')
    argv = extra_args + [
        '-o', outfile, '-q',
        '--script', UPDATER,
        '--tool_base', ':'.join(tool_path),
    ]

    # Create the shellball, extract it, and get a list of files it contains.
    self.packer.Start(argv)
    cros_build_lib.RunCommand([outfile, '--sb_extract', self.unpackdir],
                              quiet=True, mute_output=True)
    files = []
    for dirpath, _, fnames in os.walk(self.unpackdir):
      for fname in fnames:
        rel_path = os.path.join(dirpath, fname)[len(self.unpackdir) + 1:]
        files.append(rel_path)

    versions = self._ReadVersions(outfile)
    return outfile, sorted(files), versions

  def testFirmwareUpdate(self):
    """Run the firmware packer, unpack the result and check it."""
    extra_args = ['-b', os.path.join(self.indir, 'image.bin'),
                  '--stable_main_version', REEF_STABLE_MAIN_VERSION]
    outfile, files, versions = self._RunPackFirmware(extra_args)

    # Check that we got the right files.
    self.assertEqual(14, len(files))
    self.assertEqual(self._ExpectedFiles(['bios.bin']), files)

    # Comb through the VERSION file and check that everything is as expected.
    with open(os.path.join(self.unpackdir, 'VERSION')) as fd:
      lines = fd.read().splitlines()
    self.assertEqual(8, len(lines))
    self.assertEqual(
        'flashrom(8): dad068d5533fbfca9fdf42054a1ca26c '
        '*%s/functest/flashrom' % self.basedir, lines[1])
    self.assertEqual('             data', lines[2])
    self.assertEqual('             0.9.4  : 1bb61e1 : Feb 07 2017 18:29:17 UTC',
                     lines[3])
    self.assertEqual('', lines[4])
    self.assertEqual(
        'BIOS image:   99a6fc64e45596aa2c1a9911cddce952 *%s/image.bin' %
        self.indir, lines[5])
    self.assertEqual('BIOS version: Google_Reef.9042.50.0', lines[6])

    self.assertEqual('Google_Reef.9042.50.0', versions['TARGET_RO_FWID'])
    self.assertEqual('Google_Reef.9042.50.0', versions['TARGET_FWID'])
    self.assertEqual('IGNORE', versions['TARGET_ECID'])
    self.assertEqual('IGNORE', versions['TARGET_PDID'])
    self.assertEqual('Google_Reef', versions['TARGET_PLATFORM'])
    self.assertEqual(UPDATER, versions['TARGET_SCRIPT'])
    self.assertEqual(REEF_STABLE_MAIN_VERSION, versions['STABLE_FWID'])
    self.assertEqual('', versions['STABLE_ECID'])
    self.assertEqual('', versions['STABLE_PDID'])
    self.assertEqual('', versions['UNIBUILD'])
    self.assertEqual(8, len(lines))

    # Run the shellball to make sure we can do a fake autoupdate.
    self._RunScript(outfile, REEF_HWID, REEF_STABLE_MAIN_VERSION)

  def tearDown(self):
    """Remove temporary directories"""
    shutil.rmtree(self.indir)
    shutil.rmtree(self.outdir)
    shutil.rmtree(self.unpackdir)


if __name__ == '__main__':
  unittest.main()
