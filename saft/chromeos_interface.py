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

# Source of ACPI information on ChromeOS machines.
ACPI_DIR = '/sys/bus/platform/devices/chromeos_acpi'

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

    def init(self, state_dir, log_file):
        '''Initialize the ChromeOS interface object.
        Args:
          state_dir - a string, the name of the directory (as defined by the
                      caller). The contents of this directory persist over
                      system restarts and power cycles.
          log_file - a string, the name of the log file kept in the state
                     directory.
        '''
        self.state_dir = state_dir
        self.log_file = os.path.join(state_dir, log_file)

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
        '''Initialize Chrome OS interface environment.'''

        if self.target_hosted() and not os.path.exists(self.acpi_dir):
            raise ChromeOSInterfaceError(
                'ACPI directory %s not found' % self.acpi_dir)

        if os.path.exists(self.state_dir):
            raise ChromeOSInterfaceError(
                'state directory %s exists' % self.state_dir)
        try:
            os.mkdir(self.state_dir)
        except OSError, err:
            raise ChromeOSInterfaceError(err)

    def shut_down(self, new_log='/var/saft_log.txt'):
        '''Destroy temporary environment so that the test can be restarted.'''
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

        if not self.log_file:
            # called before log file name was set, ignore.
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

    def run_shell_command_get_output(self, cmd):
        '''Run shell command and return its console output to the caller.

      The output is returned as a list of strings stripped of the newline
      characters.'''

        process = self.run_shell_command(cmd)
        return [x.rstrip() for x in process.stdout.readlines()]

    def boot_state_vector(self):
        '''Read and return to caller a ':' concatenated BINF files contents.'''

        binf_fname_template = 'BINF.%d'
        state = []
        for index in range(3):
            fname = os.path.join(self.acpi_dir, binf_fname_template % index)
            state.append(open(fname, 'r').read())
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
        root_dev = self.run_shell_command_get_output('rootdev')[0]
        if dev == root_dev:
            dev = '/dev/root'

        for line in self.run_shell_command_get_output('mount'):
            if not line.startswith(dev):
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
