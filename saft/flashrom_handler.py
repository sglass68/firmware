#!/usr/bin/python
# Copyright (c) 2010 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

'''A module to support automated testing of ChromeOS firmware.

Utilizes services provided by saft_flashrom_util.py read/write the
flashrom chip and to parse the flash rom image.

See docstring for FlashromHandler class below.
'''

import struct

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

    # File in the state directory to store public root key.
    PUB_KEY_FILE_NAME = 'root.pubkey'

    def __init__(self):
    # make sure it does not accidentally overwrite the image.
        self.fum = None
        self.chros_if = None
        self.image = ''
        self.pub_key_file = ''
        self.fv_sections = {
            'a': FvImage('VBOOTA', 'FVMAIN'),
            'b': FvImage('VBOOTB', 'FVMAINB'),
            }

    def init(self, flashrom_util_module, chros_if, pub_key_file=None):
        '''Flashrom handler initializer.

        Args:
          flashrom_util_module - a module providing flashrom access utilities.
          chros_if - a module providing interface to Chromium OS services
          pub_key_file - a string, name of the file contaning a public key to
                         use for verifying both existing and new firmware.
        '''
        self.fum = flashrom_util_module.flashrom_util()
        self.chros_if = chros_if
        self.pub_key_file = pub_key_file

    def new_image(self, image_file=None):
        '''Parse the full flashrom image and store sections into files.

        Args:
          image_file - a string, the name of the file contaning full ChromeOS
                       flashrom image. If not passed in or empty - the actual
                       flashrom is read and its contents are saved into a
                       temporary file which is used instead.

        The input file is parsed and the sections of importance (as defined in
        self.fv_sections) are saved in separate files in the state directory
        as defined in the chros_if object.
        '''

        if image_file:
            self.image = open(image_file, 'rb').read()
            self.fum.set_bios_layout(image_file)
        else:
            self.image = self.fum.read_whole()

        for section in self.fv_sections.itervalues():
            for subsection_name in section.names():
                f = open(self.chros_if.state_dir_file(subsection_name), 'wb')
                f.write(self.fum.get_section(self.image, subsection_name))
                f.close()

        if not self.pub_key_file:
            self._retrieve_pub_key()

    def _retrieve_pub_key(self):
        '''Retrieve root public key from the firmware GBB section.'''

        gbb_header_format = '<4s20s2I'
        pubk_header_format = '<2Q'

        gbb_section = self.fum.get_section(self.image, 'FV_GBB')

        # do some sanity checks
        try:
            sig, _, rootk_offs, rootk_size = struct.unpack_from(
                gbb_header_format, gbb_section)
        except struct.error, e:
            raise FlashromHandlerError(e)

        if sig != '$GBB' or (rootk_offs + rootk_size) > len(gbb_section):
            raise FlashromHandlerError('Bad gbb header')

        key_body_offset, key_body_size = struct.unpack_from(
            pubk_header_format, gbb_section, rootk_offs)

        # Generally speaking the offset field can be anything, but in case of
        # GBB section the key is stored as a standalone entity, so the offset
        # of the key body is expected to be equal to the key header size of
        # 0x20.
        # Should this convention change, the check below would fail, which
        # would be a good prompt for revisiting this test's behavior and
        # algorithms.
        if key_body_offset != 0x20 or key_body_size > rootk_size:
            raise FlashromHandlerError('Bad public key format')

        # All checks passed, let's store the key in a file.
        self.pub_key_file = self.chros_if.state_dir_file(self.PUB_KEY_FILE_NAME)
        keyf = open(self.pub_key_file, 'w')
        key = gbb_section[
            rootk_offs:rootk_offs + key_body_offset + key_body_size]
        keyf.write(key)
        keyf.close()

    def verify_image(self):
        '''Confirm the image's validity.

        Using the file supplied to init() as the public key container verify
        the two sections' (FirmwareA and FirmwareB) integrity. The contents of
        the sections is taken from the files created by new_image()

        In case there is an integrity error raises FlashromHandlerError
        exception with the appropriate error message text.
        '''

        for section in self.fv_sections.itervalues():
            cmd = 'vbutil_firmware --verify %s --signpubkey %s  --fv %s' % (
                self.chros_if.state_dir_file(section.sig_name),
                self.pub_key_file,
                self.chros_if.state_dir_file(section.body_name))
            self.chros_if.run_shell_command(cmd)

    def _modify_section(self, section, delta):
        '''Modify a firmware section inside the image.

        The passed in delta is added to the value located at 5% offset into
        the section body.

        Calling this function again for the same section the complimentary
        delta value would restore the section contents.
        '''

        if not self.image:
            raise FlashromHandlerError(
                'Attempt at using an uninitialized object')
        if section not in self.fv_sections:
            raise FlashromHandlerError('Unknown FW section %s'
                                       % section)

        # Get the appropriate section of the image.
        subsection_name = self.fv_sections[section].body_name
        body = self.fum.get_section(self.image, subsection_name)

        # Modify the byte in it within 5% of the section body.
        index = len(body) / 20
        body_list = list(body)
        body_list[index] = '%c' % ((ord(body[index]) + delta) % 0x100)
        self.image = self.fum.put_section(self.image,
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
        self.fum.write_partial(self.image, (subsection_name, ))

    def restore_firmware(self, section):
        '''Restore the previously corrupted section in the FLASHROM!!!'''

        subsection_name = self.restore_section(section)
        self.fum.write_partial(self.image, (subsection_name, ))

    def write_whole(self):
        '''Write the whole image into the flashrom.'''

        if not self.image:
            raise FlashromHandlerError(
                'Attempt at using an uninitialized object')
        self.fum.write_whole(self.image)

    def dump_whole(self, filename):
        '''Write the whole image into a file.'''

        if not self.image:
            raise FlashromHandlerError(
                'Attempt at using an uninitialized object')
        open(filename, 'w').write(self.image)
