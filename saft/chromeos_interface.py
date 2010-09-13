#!/usr/bin/python
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

'''A module to provide interface to ChromeOS services.'''

import datetime
import os
import re
import shutil
import subprocess
import tempfile
import time

# Source of ACPI information on ChromeOS machines.
ACPI_DIR = '/sys/devices/platform/chromeos_acpi'

class ChromeOSInterfaceError(Exception):
    '''ChromeOS interface specific exception.'''
    pass

class ChromeOSInterface(object):
    '''An object to encapsulate OS services functions.'''

    def __init__(self, silent):
        '''Object construction time initialization.

        The only parameter is the Boolean 'silent', when True the instance
        does not duplicate log messages on the console.
        '''
        self.silent = silent
        self.state_dir = None
        self.log_file = None
        self.acpi_dir = ACPI_DIR

    def init(self, state_dir=None, log_file=None):
        '''Initialize the ChromeOS interface object.
        Args:
          state_dir - a string, the name of the directory (as defined by the
                      caller). The contents of this directory persist over
                      system restarts and power cycles.
          log_file - a string, the name of the log file kept in the state
                     directory.

        Default argument values support unit testing.
        '''
        self.state_dir = state_dir

        if state_dir and log_file:
            self.log_file = os.path.join(state_dir, log_file)
        else:
            self.log_file = None

    def target_hosted(self):
        '''Return True if running on a ChromeOS target.'''
        return 'chromeos' in open('/proc/version_signature', 'r').read()

    def state_dir_file(self, file_name):
        '''Get a full path of a file in the state directory.'''
        return os.path.join(self.state_dir, file_name)

    def acpi_file(self, file_name):
        '''Get a full path of a file in the ACPI directory.'''
        return os.path.join(self.acpi_dir, file_name)

    def init_environment(self):
        '''Initialize Chrome OS interface environment.

        If state dir was not set up by the constructor, create a temp
        directory, otherwise create the directory defined during construction
        of this object.

        Return the state directory name.
        '''

        if self.target_hosted() and not os.path.exists(self.acpi_dir):
            raise ChromeOSInterfaceError(
                'ACPI directory %s not found' % self.acpi_dir)

        if self.state_dir:
            if os.path.exists(self.state_dir):
                raise ChromeOSInterfaceError(
                    'state directory %s exists' % self.state_dir)
            try:
                os.mkdir(self.state_dir)
            except OSError, err:
                raise ChromeOSInterfaceError(err)
        else:
            self.state_dir = tempfile.mkdtemp()
        return self.state_dir

    def shut_down(self, new_log='/var/saft_log.txt'):
        '''Destroy temporary environment so that the test can be restarted.'''
        if os.path.exists(self.log_file):
            shutil.copyfile(self.log_file, new_log)
        shutil.rmtree(self.state_dir)

    def log(self, text):
        '''Write text to the log file and print it on the screen, if enabled.

      The entire log (maintained across reboots) can be found in
      self.log_file.
      '''

        # Don't print on the screen unless enabled.
        if not self.silent:
            print text

        if not self.log_file or not os.path.exists(self.state_dir):
            # Called before environment was initialized, ignore.
            return

        timestamp = datetime.datetime.strftime(
            datetime.datetime.now(), '%I:%M:%S %p:')

        log_f = open(self.log_file, 'a')
        log_f.write('%s %s\n' % (timestamp, text))
        log_f.close()

    def exec_exists(self, program):
        '''Check if the passed in string is a valid executable found in PATH.'''

        for path in os.environ['PATH'].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if (os.path.isfile(exe_file) or os.path.islink(exe_file)
                ) and os.access(exe_file, os.X_OK):
                return True
        return False

    def run_shell_command(self, cmd):
        '''Run a shell command.

      In case of the command returning an error print its stdout and stderr
      outputs on the console and dump them into the log. Otherwise suppress all
      output.

      In case of command error raise an OSInterfaceError exception.

      Return the subprocess.Popen() instance to provide access to console
      output in case command succeeded.
      '''

        self.log('Executing %s' % cmd)
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        process.wait()
        if process.returncode:
            err = ['Failed running: %s' % cmd]
            err.append('stdout:')
            err.append(process.stdout.read())
            err.append('stderr:')
            err.append(process.stderr.read())
            text = '\n'.join(err)
            print text
            self.log(text)
            raise ChromeOSInterfaceError('command %s failed' % cmd)
        return process

    def is_removable_device(self, device):
        '''Check if a certain storage device is removable.

        device - a string, file name of a storage device or a device partition
                 (as in /dev/sda[0-9]).

        Returns True if the device is removable, False if not.
        '''

        # Drop trailing digit(s) and letter(s) (if any)
        dev_name_stripper = re.compile('[0-9].*$')

        base_dev = dev_name_stripper.sub('', device.split('/')[2])
        removable = int(open('/sys/block/%s/removable' % base_dev, 'r'
                             ).read())

        return removable == 1

    def get_root_dev(self):
        '''Return a string, the name of the root device'''
        return self.run_shell_command_get_output('rootdev -s')[0]

    def run_shell_command_get_output(self, cmd):
        '''Run shell command and return its console output to the caller.

      The output is returned as a list of strings stripped of the newline
      characters.'''

        process = self.run_shell_command(cmd)
        return [x.rstrip() for x in process.stdout.readlines()]

    def boot_state_vector(self):
        '''Read and return to caller a string describing the system state.

        The string has a form of x:x:x:<removable>:<partition_number>, where
        x' represent contents of the appropriate BINF files as reported by
        ACPI, <removable> is set to 1 or 0 depending if the root device is
        removable or not, and <partition number> is the last element of the
        root device name, designating the partition where the root fs is
        mounted.

        This vector fully describes the way the system came up.
        '''

        binf_fname_template = 'BINF.%d'
        state = []
        for index in range(3):
            fname = os.path.join(self.acpi_dir, binf_fname_template % index)
            max_wait = 30
            cycles = 0
            # In some cases (for instance when running off the flash file
            # system) the ACPI files go not get created right away. Let's give
            # it some time to settle.
            while not os.path.exists(fname):
                if cycles == max_wait:
                    self.log('%s is not present' % fname)
                    raise AssertionError
                time.sleep(1)
                cycles += 1
            if cycles:
                self.log('ACPI took %d cycles' % cycles)
            state.append(open(fname, 'r').read())
        root_dev = self.get_root_dev()
        state.append('%d' % int(self.is_removable_device(root_dev)))
        state.append('%s' % root_dev[-1])
        state_str = ':'.join(state)
        return state_str

    def get_writeable_mount_point(self, dev, tmp_dir):
        '''Get mountpoint of the passed in device mounted in read/write mode.

      If the device is already mounted and is writeable - return its mount
      point. If the device is mounted but read-only - remount it read/write
      and return its mount point. If the device is not mounted - mount it read
      write on the passsed in path and return this path.
      '''

      # The device root file system is mounted on is represented as /dev/root
      # otherwise.
        options_filter = re.compile('.*\((.+)\).*')
        root_dev = self.get_root_dev()
        if dev == root_dev:
            dev = '/dev/root'

        for line in self.run_shell_command_get_output('mount'):
            if not line.startswith('%s ' % dev):
                continue
            mount_options = options_filter.match(line).groups(0)[0]
        # found mounted
            if 'ro' in mount_options.split(','):
          # mounted read only
                self.run_shell_command('mount -o remount,rw %s' % dev)
            return line.split()[2]  # Mountpoint is the third element.
      # Not found, needs to be mounted
        self.run_shell_command('mount %s %s' % (dev, tmp_dir))
        return tmp_dir
