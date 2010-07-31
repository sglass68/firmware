#!/usr/bin/python
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

'''A module to support automated testing of ChromeOS formware.

Utilizes services provided by <board arch>/flashrom_util.py read/write the
flashrom chip and to parse the flash rom image.

See docstring for FlashromHandler class below.
'''

import os
import subprocess


class FvImage(object):
    '''An object to hold names of matching signature and firmware sections.'''

    def __init__(self, sig_name, body_name):
        self.sig_name = sig_name
        self.body_name = body_name

    def names(self):
        return (self.sig_name, self.body_name)


class FlashromHandlerError(Exception):
    pass


class FlashromHandler(object):
    '''An object to provide logical services for automated flashrom testing.'''

    DELTA = 1  # value to add to a byte to corrupt a section contents

    def __init__(self):
    # make sure it does not accidentally overwrite the image.
        self.fum = None
        self.bios_layout = None
        self.state_dir = None
        self.image = ''
        self.pub_key_file = ''
        self.fv_sections = {
            'a': FvImage('VBOOTA', 'FVMAIN'),
            'b': FvImage('VBOOTB', 'FVMAINB'),
            }

    def init(self, flashrom_util_module, state_dir, pub_key_file,):
    # make sure it does not accidentally overwrite the image.
        self.fum = flashrom_util_module.flashrom_util()
        self.state_dir = state_dir
        self.pub_key_file = pub_key_file

    def _state_file(self, name):
        '''Return full path name of a given file in the state directory.'''

        return os.path.join(self.state_dir, name)

    def new_image(self, image_file=None):
        '''Parse the full flashrom image and store sections into files.

    Args:
      image_file - a string, the name of the file contaning full ChromeOS
                   flashrom image. If not passed in or empty - the actual
                   flashrom is read and its contents are saved into a temp.
                   file which is used instead.

    The input file is parsed and the sections of importance (as defined in
    self.fv_sections) are saved in separate files in self.state_dir.

    '''

        if image_file:
            self.image = open(image_file, 'rb').read()
        else:
            self.image = self.fum.read_whole()
        self.bios_layout = self.fum.detect_chromeos_layout('bios',
                len(self.image))
        self.whole_flash_layout = self.fum.detect_layout('all',
                len(self.image))

        for section in self.fv_sections.itervalues():
            for subsection_name in section.names():
                f = open(self._state_file(subsection_name), 'wb')
                f.write(self.fum.get_section(self.image,
                                             self.bios_layout, subsection_name))
                f.close()

    def verify_image(self):
        '''Confirm the image's validity.

    Using the file supplied to init() as the public key container verify the
    two sections' (FirmwareA and FirmwareB) integrity. The contents of the
    sections is taken from the files created by new_image()

    In case there is an integrity error raises FlashromHandlerError exception
    with the appropriate error message text.
    '''

        for section in self.fv_sections.itervalues():
            cmd = 'vbutil_firmware --verify %s --signpubkey %s  --fv %s' % (
                self._state_file(section.sig_name),
                self.pub_key_file,
                self._state_file(section.body_name))
            p = subprocess.Popen(cmd.split(), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            p.wait()
            if p.returncode:
                raise FlashromHandlerError('Failed verifying %s'
                                           % section.body_name)

    def _modify_section(self, section, delta):
        '''Modify a firmware section inside the image.

    The passed in delta is added to the value located at 5% offset into the
    section body.

    Calling this function again for the same section the complimentary delta
    value would restore the section contents.
    '''

        if not self.image:
            raise FlashromHandlerError(
                'Attempt at using an uninitialized object')
        if section not in self.fv_sections:
            raise FlashromHandlerError('Unknown FW section %s'
                                       % section)
    # get the appropriate section of the image
        subsection_name = self.fv_sections[section].body_name
        body = self.fum.get_section(self.image, self.bios_layout,
                                    subsection_name)

    # corrupt a byte in it
        index = len(body) / 20
        body_list = list(body)
        body_list[index] = '%c' % ((ord(body[index]) + delta) % 0x100)
        self.image = self.fum.put_section(self.image, self.bios_layout,
                                          subsection_name, ''.join(body_list))
        return subsection_name

    def corrupt_section(self, section):
        '''Corrupt a section of the image'''

        return self._modify_section(section, self.DELTA)

    def restore_section(self, section):
        '''Restore a previously corrupted section of the image.'''

        return self._modify_section(section, -self.DELTA)

    def corrupt_firmware(self, section):
        '''Corrupt a section in the FLASHROM!!!'''

        subsection_name = self.corrupt_section(section)
        self.fum.write_partial(self.image, self.bios_layout,
                               (subsection_name, ))

    def restore_firmware(self, section):
        '''Restore the previously corrupted section in the FLASHROM!!!'''

        subsection_name = self.restore_section(section)
        self.fum.write_partial(self.image, self.bios_layout,
                               (subsection_name, ))

    def write_whole(self):
        '''Write the whole image into the flashrom.'''

        if not self.image:
            raise FlashromHandlerError(
                'Attempt at using an uninitialized object')
        self.fum.write_partial(self.image, self.whole_flash_layout, ('all', ))

    def dump_whole(self, filename):
        '''Write the whole image into a file.'''

        if not self.image:
            raise FlashromHandlerError(
                'Attempt at using an uninitialized object')
        open(filename, 'w').write(self.image)
