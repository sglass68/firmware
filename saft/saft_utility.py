#!/usr/bin/python
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

'''Test utility for verifying ChromeOS firmware.'''

import datetime
import getopt
import os
import re
import shutil
import sys
import tempfile

import chromeos_interface
import flashrom_handler
import flashrom_util
import kernel_handler

from functools import wraps

# This is the name of the upstart script generated by this utility. The script
# ensures that the test continues after every reboot until failed or done.
UPSTART_SCRIPT = '/etc/init/fw_test.conf'

# This is a template used to populate the upstart script described above.
UPSTART_SCRIPT_TEMPLATE = '''
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Do not edit, this file was autogenerated.

start on started ui

script
  exec > %s 2>&1
  local dev="%s"
  local mount_point
  rootdev=$(rootdev | sed 's|.*/||')
  if [ "${dev}" = "${rootdev}" ]; then
    mount_point=""
  else
    retry=0
    blkdev=/sys/block/$(echo "${dev}" | sed 's/[0-9]$//')
    while [ ! -e "${blkdev}" ]
    do
      if [ "${retry}" = "15" ]; then
        echo "USB flash drive ${blkdev} is not ready"
        exit 1
      fi
      sleep 1
      retry=$((retry + 1))
    done
    if [ "${retry}" != "0" ]; then
      echo "Detected USB device after ${retry} retry(ies)"
    fi
    # Is it already mounted? The check below will generate
    # an error if there is more than one mount point.
    mount_point=$(mount |
      awk "/^\/dev\/${dev}/  {if (mp) { exit 1 }  mp=\$3} END { print mp }")
    if [ -z "${mount_point}" ]; then
      # No, it's not, let's mount it.
      mount_point="/tmp/${dev}"
      mkdir "${mount_point}"
      mount "/dev/${dev}"  "${mount_point}"
    fi
  fi

  cd "${mount_point}%s"
  PYTHONPATH=$(realpath ../x86-generic) ./%s --next_step
end script

console output

'''

# Subdirectory to keep the state of this test over reboots. Created in the
# /var on the USB flash drive the test is running from.
STATE_SUBDIR = '.fw_test'

# Files storing SAFT state over reboots, located in state_dir defined below.
LOG_FILE = 'fw_test_log.txt'  # Test log.
STEP_FILE = 'fw_test_state'  # Test the step number.
FW_BACKUP_FILE = 'flashrom.bak'  # Preserved original flashrom contents.
FW_COPY_FILE = 'flashrom.new'  # A copy of the flashrom contents being tested.
FWID_BACKUP_FILE = 'fwid.bak'  # FWID reported by the original firmware.
FWID_NEW_FILE = 'fwid.new'  # FWID reported by the firmware being tested.

# The list of shell executables necessary for this program to work.
REQUIRED_PROGRAMS = '''
cgpt blkid flashrom reboot_mode realpath rootdev vbutil_firmware vbutil_kernel
'''

# File where the startup script forwards its stdio and stderr
STARTUP_LOG_FILE = '/tmp/fw_test.out.txt'

FLASHROM_HANDLER = flashrom_handler.FlashromHandler()
CHROS_IF = chromeos_interface.ChromeOSInterface(__name__ != '__main__')

def allow_multiple_section_input(image_operator):
    @wraps(image_operator)
    def wrapper(self, section):
        if type(section) is not tuple:
            image_operator(self, section)
        else:
            for sec in section:
                image_operator(self, sec)
    return wrapper

class FwError(Exception):
    '''A class to encapsulate this module specific exceptions.'''
    pass

class FirmwareTest(object):
    '''An object to represent a Firmware Semi Automated test (SAFT).

    It follows the steps as enumerated in test_state_sequence below,
    restarting the target after each step. The first time around the
    init_fw_test() method must be called to set up the persistent environment.
    All following invocations should initialize the object (using .init()) and
    the invoke the next_step() method.
    '''

    def __init__(self):
        '''Object initializer, does nothing to make mocking easier.'''
        self.mydir = None
        self.base_partition = None
        self.chros_if = None
        self.progname = None
        self.test_state_sequence = None
        self.kern_handler = None

    def _verify_fw_id(self, compare_to_file):
        '''Verify if the current firmware ID matches the contents a file.

        compare_to_file - a string, name of the file in the state directory.
        '''
        old_fwid = open(
            self.chros_if.state_dir_file(compare_to_file), 'r').read()
        now_fwid = open(self.chros_if.acpi_file('FWID'), 'r').read()
        return old_fwid == now_fwid

    def _get_step(self):
        '''Set the current value of SAFT step number.'''
        step_file = self.chros_if.state_dir_file(STEP_FILE)
        step = open(step_file, 'r').read().strip()
        return int(step)

    def _set_step(self, step):
        '''Set the SAFT step number to control the next test pass.'''
        step_file = self.chros_if.state_dir_file(STEP_FILE)
        open(step_file, 'w').write('%d' % step)

    def _handle_upstart_script(self, install):
        '''Install or remove the SAFT upstart script.

      When the test prepares to start, this function is invoked to install the
      upstart script ensuring that each time the machine resets, it executes
      this program passing it the --next_step as a parameter.

      The script needs to be installed in three places: userland A, userland B
      and the recovery userland (on the USB flash drive).

      When the test finishes, it invokes this function to remove the upstart
      scripts from all three locations.
      '''

        if not self.chros_if.target_hosted():
            print 'bypassing upstart management'
            return

        label_pattern = re.compile('/dev/(.+): LABEL="(H-ROOT-|C-KEYFOB)')
        tmp_dir = tempfile.mkdtemp()

        # Find out our file name off the root of the device
        mount_point = (self.chros_if.run_shell_command_get_output(
                'df %s' % self.mydir)[-1]).split()[-1]
        this_dir = self.mydir[len(mount_point):]

        for line in self.chros_if.run_shell_command_get_output('blkid'):
            match = label_pattern.search(line)
            if not match:
                continue
            dev = match.groups(0)[0]
            mount_point = self.chros_if.get_writeable_mount_point(
                '/dev/' + dev, tmp_dir)
            file_name = os.path.join(mount_point, UPSTART_SCRIPT.lstrip('/'))
            self.chros_if.log('Handling ' + file_name)
            if install:
                upstart_script = open(file_name, 'w')
                upstart_script.write(UPSTART_SCRIPT_TEMPLATE % (
                        STARTUP_LOG_FILE, self.base_partition.split('/')[-1],
                        this_dir, self.progname))
                upstart_script.close()
            else:
                os.unlink(file_name)
            if mount_point.startswith(tmp_dir):
                self.chros_if.run_shell_command('umount %s' % tmp_dir)

        os.rmdir(tmp_dir)

    def _check_runtime_env(self):
        '''Ensure that the script is running in proper environment.

      This involves checking that the script is running off a removable
      device, configuring proper file names for logging, etc.
      '''
        line = self.chros_if.run_shell_command_get_output(
            'df %s' % self.mydir)[-1]

        self.base_partition = line.split()[0]
        if self.base_partition == '/dev/root':
            self.base_partition = self.chros_if.get_root_dev()

        if not self.chros_if.is_removable_device(self.base_partition):
            raise FwError(
                'This test must run off a removable device, not %s'
                % self.base_partition)

        env_root = '/var'
        state_fs = '%s1' % self.base_partition[:-1]

      # is state file system mounted?
        for line in self.chros_if.run_shell_command_get_output('mount'):
            if line.startswith('%s on ' % state_fs):
                mount_point = line.split()[2]
                state_root = mount_point + env_root
        else:
            tmp_dir = tempfile.mkdtemp()
            self.chros_if.run_shell_command('mount %s %s' % (state_fs, tmp_dir))
            state_root = '%s%s' % (tmp_dir, env_root)

        self.chros_if.init(os.path.join(state_root, STATE_SUBDIR), LOG_FILE)

    def init(self, progname, chros_if, kern_handler=None, state_sequence=None):
        '''Initialize the Firmware self test instance.

        progname - a string, name of this program as it was invoked.

        chros_if - an object of ChromeOSInterface type to be initialized and
                   used by this instance of FirmwareTest.

        kern_handler - an object providing SAFT with services manipulating
                   kernel images. Unittest does not provide this object.

        test_state_sequence - a tuple of three-tuples driving test execution,
                   see description below. Unittest does not provide this
                   sequence.
        '''
        real_name = os.path.realpath(progname)
        self.mydir = os.path.dirname(real_name)
        self.progname = os.path.basename(real_name)
        self.chros_if = chros_if
        self._check_runtime_env()
        self.test_state_sequence = state_sequence
        if kern_handler:
            self.kern_handler = kern_handler
            self.kern_handler.init(self.chros_if)

    def set_try_fw_b(self):
        '''Request running firmware B on the next restart.'''
        self.chros_if.log('Requesting restart with FW B')
        self.chros_if.run_shell_command('reboot_mode --try_firmware_b=1')

    @allow_multiple_section_input
    def restore_firmware(self, section):
        '''Restore the requested firmware section (previously corrupted).'''
        self.chros_if.log('Restoring firmware %s' % section)
        FLASHROM_HANDLER.new_image()
        FLASHROM_HANDLER.restore_firmware(section)

    @allow_multiple_section_input
    def corrupt_firmware(self, section):
        '''Corrupt the requested firmware section.'''
        self.chros_if.log('Corrupting firmware %s' % section)
        FLASHROM_HANDLER.new_image()
        FLASHROM_HANDLER.corrupt_firmware(section)

    @allow_multiple_section_input
    def corrupt_kernel(self, section):
        '''Corrupt the requested kernel section.'''
        self.chros_if.log('Corrupting kernel %s' % section)
        self.kern_handler.corrupt_kernel(section)

    @allow_multiple_section_input
    def restore_kernel(self, section):
        '''restore the requested kernel section.'''
        self.chros_if.log('restoring kernel %s' % section)
        self.kern_handler.restore_kernel(section)

    def revert_firmware(self):
        '''Restore firmware to the image backed up when SAFT started.'''
        self.chros_if.log('restoring original firmware image')
        self.chros_if.run_shell_command(
            'flashrom -w %s' % self.chros_if.state_dir_file(FW_BACKUP_FILE))

    def init_fw_test(self, opt_dictionary, chros_if):
        '''Prepare firmware test context.

      This function tries creating the state directory for the fw test and
      initializes the test state machine.

      Return
        True on success
        False on any failure or if the directory already exists
      '''
        chros_if.init_environment()
        chros_if.log('Automated firmware test log generated on %s' % (
                datetime.datetime.strftime(datetime.datetime.now(),
                                               '%b %d %Y')))
        chros_if.log('Original boot state %s' % chros_if.boot_state_vector())
        self.chros_if = chros_if
        fw_image = opt_dictionary['image_file']
        FLASHROM_HANDLER.new_image()
        FLASHROM_HANDLER.verify_image()
        FLASHROM_HANDLER.dump_whole(
            self.chros_if.state_dir_file(FW_BACKUP_FILE))
        FLASHROM_HANDLER.new_image(fw_image)
        FLASHROM_HANDLER.verify_image()
        self._handle_upstart_script(True)
        shutil.copyfile(self.chros_if.acpi_file('FWID'),
                        self.chros_if.state_dir_file(FWID_BACKUP_FILE))
        shutil.copyfile(fw_image, self.chros_if.state_dir_file(FW_COPY_FILE))
        self._set_step(0)

    def next_step(self):
        '''Function to execute a single SAFT step.

      This function is running after each reboot. It determines the current
      step the SAFT is on, executes the appropriate action, increments the
      step value and then restats the machine.
      '''

        this_step = self._get_step()

        if this_step == 0:
            shutil.copyfile(self.chros_if.acpi_file('FWID'),
                            self.chros_if.state_dir_file(FWID_NEW_FILE))

            if self._verify_fw_id(FWID_BACKUP_FILE):
          # we expected FWID to change, but it did not - have the firmware
          # been even replaced?
                self.chros_if.log('New firmware - old FWID')
                sys.exit(1)
        test_state_tuple = self.test_state_sequence[this_step]
        expected_vector = test_state_tuple[0]
        action = test_state_tuple[1]
        boot_vector = self.chros_if.boot_state_vector()
        self.chros_if.log('Rebooted into state %s on step %d' % (
                boot_vector, this_step))

        if os.path.exists(STARTUP_LOG_FILE):
            contents = open(STARTUP_LOG_FILE).read().rstrip()
            if len(contents):
                self.chros_if.log('startup log contents:')
                self.chros_if.log(contents)

        if boot_vector != expected_vector:
            self.chros_if.log('Error: Wrong boot vector, %s was expected'
                % expected_vector)
            sys.exit(1)
        if this_step == (len(self.test_state_sequence) - 1):
            self.finish_saft()
        if action and not self._verify_fw_id(FWID_NEW_FILE):
            self.chros_if.log('Error: Wrong FWID value')
            sys.exit(1)

        if action:
            if len(test_state_tuple) > 2:
                self.chros_if.log('calling %s with parameter %s' % (
                        str(action), str(test_state_tuple[2])))
                action(test_state_tuple[2])
            else:
                self.chros_if.log('calling %s' % str(action))
                action()
        self._set_step(this_step + 1)
        self.chros_if.run_shell_command('reboot')

    def finish_saft(self):
        '''SAFT is done, restore the environment and expose the results.

        Restore to the original firmware, delete the startup scripts from all
        drives, copy the log to the stateful partition on the flash drive and
        restart the machine.
        '''
        if not self._verify_fw_id(FWID_BACKUP_FILE):
            self.chros_if.log(
                'Error: Failed to restore to original firmware')
            sys.exit(1)
        self.chros_if.log('Removing upstart scripts')
        self._handle_upstart_script(False)
        self.chros_if.log('we are done!')

        # Shut_down will delete the SAFT state directory, let's have the log
        # copied one level up.
        dummy = self.chros_if.state_dir_file('dummy')
        log_dst_dir = os.path.realpath(os.path.join(os.path.dirname(dummy), '..'))
        self.chros_if.shut_down(os.path.join(log_dst_dir, LOG_FILE))
        self.chros_if.run_shell_command('reboot')


# Firmware self test instance controlling this module.
FST = FirmwareTest()

# This is a tuple of tuples controlling the SAFT state machine. The states
# are expected to be passed strictly in order. The states are identified
# by the contents of BINF.[012] files in the sys fs ACPI directory. The
# BINF files store information about the reason for reboot, what
# firmware/kernel partitions were used, etc.
#
# The first element of each component tuple is the expected state of the
# machine (a ':' concatenation of the BINF files' contents).
#
# The second element of the component tuples is the action to take to
# advance the test. The action is a function to call. The last line has
# action set to None, which indicates to the state machine that the test
# is over.
#
# The third component, if present, is the parameter to pass to the action
# function.

TEST_STATE_SEQUENCE = (
    ('1:1:0:0:3', FST.set_try_fw_b),
    ('1:2:0:0:3', None),
    ('1:1:0:0:3', FST.corrupt_firmware, 'a'),
    ('1:2:0:0:3', FST.restore_firmware, 'a'),
    ('1:1:0:0:3', FST.corrupt_firmware, ('a', 'b')),
    ('5:0:0:1:3', FST.restore_firmware, ('a', 'b')),
    ('1:1:0:0:3', FST.corrupt_kernel, 'a'),
    ('1:1:0:0:5', FST.restore_kernel, 'a'),
    ('1:1:0:0:3', FST.revert_firmware),
    ('1:1:0:0:3', None),
    )


# The string below serves two purposes:
#
# - spell out the usage string for this program
#
# - provide text for parsing to derive command line flags. The text is split
#   to words, then the words which have -- in them are stripped off the
#   leading/traling brackets and dashes and used to prepare inut parameters
#   for getopt. This is the only way to pass the parameters to getopt, this
#   enforces that each accepted parameter is mentioned in the usage() output.
USAGE_STRING = '''
 [--image_file=<firmware_image_file>] [--pub_key=<file>] [--next_step]

  The program can be invoked in two modes.

  When invoked for the first time, most of the parameters are required to set
  up the test context and start it.

  Specifying --next_step means that the program is being invoked by the
  restarted system, all the context is expected to be available. No other
  parameters are required or expected in that case.
'''


def usage(msg='', retv=0):
    '''Print error message (if any), usage string and exit.

    Depending on the passed in return value use stdout (if retv is 0) or
    stderr (if otherwise).
    '''
    progname = os.path.basename(sys.argv[0])
    if retv:
        ofile = sys.stderr
    else:
        ofile = sys.stdout
    if msg:
        print >> ofile, '%s: %s' % (progname, msg)
        CHROS_IF.log(msg)
    print >> ofile, 'usage: %s %s' % (progname, USAGE_STRING.strip())
    sys.exit(retv)


def get_options_set():
    '''Generate the list of command line options accepted by this program.

  This function derives the set of accepted command line options from
  usage_string. The string is split into words, all words starting with -- are
  considered accepted command line parameters. The presence of an '=' sign in
  the word,means that the command line parameter requires a value.

  Returns a list of strings suitable for use by the getopt module.
  '''

    drop_tail = re.compile('=.*$')
    option_set = []
    items = (' '.join(USAGE_STRING.split('\n'))).split(' ')
    for item in items:
        if '--' in item:
            if item.startswith("'"):
                continue
            option = drop_tail.sub('=', item.strip('-[]'))
            if option not in option_set:
                option_set.append(option)
    return option_set



def main(argv):
    '''Process command line options and invoke the proper test entry point.'''
    (opts, params) = getopt.gnu_getopt(argv[1:], '', get_options_set())
    if params:
        raise FwError('unrecognized parameters: %s' % ' '.join(params))

    opt_dictionary = {}
    for (name, value) in opts:
        opt_dictionary[name.lstrip('-')] = value

    FST.init(argv[0], CHROS_IF,
             kernel_handler.KernelHandler(), TEST_STATE_SEQUENCE)

    FLASHROM_HANDLER.init(flashrom_util, CHROS_IF,
                         opt_dictionary.get('pub_key'))
    if 'next_step' in opt_dictionary:
        if len(opt_dictionary) != 1:
            usage('--next_step (when specified) must be the only parameter', 1)
        FST.next_step()
        sys.exit(0)

    # check if all executables are available
    missing_execs = []
    for prog in REQUIRED_PROGRAMS.split():
        if not prog:
            continue # ignore line breaks
        if not CHROS_IF.exec_exists(prog):
            missing_execs.append(prog)
    if missing_execs:
        usage('Program(s) %s not found in PATH' % ' '.join(missing_execs), 1)

    if 'image_file' not in opt_dictionary:
        usage(retv=1)

    FST.init_fw_test(opt_dictionary, CHROS_IF)
    CHROS_IF.log('program the new image')
    FLASHROM_HANDLER.write_whole()
    CHROS_IF.log('restart')
    CHROS_IF.run_shell_command('reboot')
    return 0


if __name__ == '__main__':
    try:
        main(sys.argv)
    except (getopt.GetoptError, ImportError):
        usage(sys.exc_info()[1], 1)
    except (FwError, flashrom_handler.FlashromHandlerError):
        MSG = 'Error: %s' % str(sys.exc_info()[1])
        print MSG
        CHROS_IF.log(MSG)
        sys.exit(1)

    sys.exit(0)
