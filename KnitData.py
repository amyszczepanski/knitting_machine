#!/usr/bin/env python

# Copyright 2012  Steve Conklin 
# steve at conklinhouse dot com
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.

import sys
import os
import array
#import os.path
#import string
import csv
import time
import serial
import Image

# Some file location constants
ADDR_INITIAL_PATTERN_OFFSET = 0x06DF # programmed patterns start here, grow down
ADDR_CURRENT_PATTERN_ADDR = 0x07EA # stored in MSN and following byte
ADDR_CURRENT_ROW = 0x06FF
ADDR_NEXT_ROW = 0x072F
ADDR_CURRENT_ROW_NUMBER = 0x0702
ADDR_CARRIAGE_STATUS = 0x070F
ADDR_SELECT = 0x07EA

# some size constants
SIZE_PATTERN_DIR = 7

# NEEDLE_DATA_LEN = 13 # 13 bytes for 200 needles

# Pattern variations
# VARIATION_REV = 0x01 # reverse
# VARIATION_MH = 0x02  # mirror horizontal
# VARIATION_SH = 0x08  # stretch horizontal
# VARIATION_SV = 0x10  # stretch vertical
# VARIATION_IV = 0x04  # invert vertical
# VARIATION_KHC = 0x20 # KHC
# VARIATION_KRC = 0x40 # KRC
# VARIATION_MB = 0x80  # M button

# memory ranges by usage
mem_ranges = {
    'pattern' : range(0, 0x06e0),
    'needle'  : range(0x06e6, 0x0700) + range(0x0716, 0x0730),
    'motif'   : range(0x07ed, 0x0800)
}


# A dictionary of memory addresses and their use (if known) -
# This is used for debug and reverse engineering
mem_map = {
    0x06e0 : 'pressing M changes this to 0x81 forever',
    0x06e1 : 'pressing M changes this to 0x02 forever',
    0x06e2 : 'Unknown',
    0x06e3 : 'Unknown',
    0x06e4 : 'Unknown',
    0x06e5 : 'Always 1766?',
    0x06e6 : 'End (lowest byte) of current row needle pattern',
    0x06ff : 'Start (highest byte) of current row needle pattern',
    0x0700 : 'On memory clear init to 01',
    0x0701 : 'On memory clear init to 0x20, known to change',
    0x0702 : 'LSN is current row number hundreds',
    0x0703 : 'Current Row Number',
    #
    0x070d : 'Variations and M button status',
    0x070e : 'Changes when the left end of pattern in Selection 1 is changed',
    0x070f : 'Carriage Status (direction)',
    0x0710 : 'Always 0x07?',
    0x0711 : 'Always 0xf9?',
    #
    0x0715 : 'Changes to 01 when M set, stays when M unset',
    0x0716 : 'End (lowest byte) of next row needle pattern',
    0x072f : 'Start (highest byte) of next row needle pattern',
    #
    0x0738 : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x073A : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x073C : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x073E : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x0740 : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x0742 : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x0744 : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x0747 : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x074B : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x074D : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x0750 : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x0752 : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x0756 : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x075A : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x075E : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x07D4 : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x07D5 : 'Changes to 0x20 when M set, stays when M unset',
    #
    0x07E5 : 'Changes to 0x20 when M set, stays when M unset',
    0x07E6 : 'Changes to 0x20 when M set, stays when M unset',
    0x07E7 : 'Changes to 0x20 when M set, stays when M unset',
    0x07E8 : 'Changes to 0x20 when M set, stays when M unset',
    0x07E9 : 'Changes to 0x20 when M set, stays when M unset',
    0x07ea : 'MSN - selector position, LSN - Current Pattern Number hundreds',
    0x07eb : 'Current Pattern Number tens, ones',
    0x07ec : ' MSN - unknown, LSN - Motif 6 posn hundreds',
    # Note, motif position hundreds nibbles have 0x80 set if RIGHT side
    0x07ed : 'Motif 6 posn tens, ones',
    0x07ee : 'Motif 6 copies hundreds, tens',
    0x07EF : 'Motif 6 copies ones, Motif 5 position hundreds',
    0x07F0 : 'Motif 5 position tens, ones',
    0x07F1 : 'Motif 5 copies hundreds, tens',
    0x07F2 : 'Motif 5 copies ones, Motif 4 position hundreds',
    0x07F3 : 'Motif 4 position tens, ones',
    0x07F4 : 'Motif 4 copies hundreds, tens',
    0x07F5 : 'Motif 4 copies ones, Motif 3 position hundreds',
    0x07F6 : 'Motif 3 position tens, ones',
    0x07F7 : 'Motif 3 copies hundreds, tens',
    0x07F8 : 'Motif 3 copies ones, Motif 2 position hundreds',
    0x07F9 : 'Motif 2 position tens, ones',
    0x07FA : 'Motif 2 copies hundreds, tens', 
    0x07FB : 'Motif 2 copies ones, Motif 1 position hundreds',
    0x07FC : 'Motif 1 position tens, ones',
    0x07FD : 'Motif 1 copies hundreds, tens',
    0x07fe : 'Selector 1 position hundreds',
    0x07ff : 'Selector 1 position tens, ones'
}

    

# various unknowns which are probably something we care about
unknownList = {'0700':0x0700, '0701':0x0701,
               '0704':0x0704, '0705':0x0705, '0706':0x0706, '0707':0x0707,
               '0708':0x0708, '0709':0x0709, '070A':0x070A, '070B':0x070B,
               '070C':0x070C, '070D':0x070D, '070E':0x070E, '0710':0x0710,
               '0711':0x0711, '0712':0x0712, '0713':0x0713, '0714':0x0714,
               '0715':0x0715}


FILTER=''.join([(len(repr(chr(x)))==3) and chr(x) or '.' for x in range(256)])

def dump(src, length=16):
    """
    Dump the data in hex and ascii (of printable)
    """
    result=[]
    for i in xrange(0, len(src), length):
       s = src[i:i+length]
       hexa = ' '.join(["%02X"%ord(x) for x in s])
       printable = s.translate(FILTER)
       result.append("%04X   %-*s   %s\n" % (i, length*3, hexa, printable))
    return ''.join(result)

def nibbles(achar):
    """
    split a byte into most and least significant nibbles, and return them
    """
    msn = (ord(achar) & 0xF0) >> 4
    lsn = ord(achar) & 0x0F
    return msn, lsn

def hto(hundreds, tens, ones):
    """
    return an int resulting from the addition of the digits
    """
    return (100 * hundreds) + (10 * tens) + ones

def bytesForMemo(rows):
    """
    Calculate and return the number of Memo bytes required for a pattern with
    a given number of rows
    """
    bytes = roundeven(rows)/2
    return bytes

def nibblesPerRow(stitches):
    # there are four stitches per nibble
    # each row is nibble aligned
    return(roundfour(stitches)/4)

def bytesPerPattern(stitches, rows):
    nibbs = rows * nibblesPerRow(stitches)
    bytes = roundeven(nibbs)/2
    return bytes

def bytesPerPatternAndMemo(stitches, rows):
    patbytes = bytesPerPattern(stitches, rows)
    memobytes = bytesForMemo(rows)
    return patbytes + memobytes

def roundeven(val):
    return (val+(val%2))

def roundeight(val):
    if val % 8:
        return val + (8-(val%8))
    else:
        return val

def roundfour(val):
    if val % 4:
        return val + (4-(val%4))
    else:
        return val

class Pattern():
    """
    This class encapsulates all information needed to define a pattern for the Brother
    KH930 knitting machine.
    UNDER DEVELOPMENT NOT FUNCTIONAL
    """

    def __init__(self):
        self.verbose = False
        self.number = 0
        self.rowdata = None
        self.memodata = None
        self.numStitches = 0
        self.numRows = 0
        self.memo = None # memo offset in memory
        self.offset = None # pattern offset in memory
        self.pattend = None # pattern pointer (pptr)
        self.unk_nibble = None
        self.unknown = None
        return

    def initFromFileData(self, knitData, patternNumber):
        """
        Find the specified pattern number in the data file,
        and initialize a Pattern Object from the file data
        """
        return

    def delete(self):
        return

    def asciiDump(self, pattern_number):
        pd = self.patterns[pattern_number]
        rd = pd['rowdata']
        for row in range(pd['rows']):
            for stitch in range(pd['stitches']):
                if(rd[row][stitch]) == 0:
                    print ' ',
                else:
                    print '*',
            print

    def outputImgFile(self, pattern_number, outfilename):
        """
        Export the specified pattern to an image file.
        File type is indicated by the file extension.
        Types supported are those supported by the underlying
        python Image module
        """
        pd = self.patterns[pattern_number]
        rd = pd['rowdata']
        rows = pd['rows']
        stitches = pd['stitches']

        img = Image.new("1", (stitches, rows))
        # Fill in pixel data
        pix = img.load()
        for row in range(rows):
            for stitch in range(stitches):
                if rd[row][stitch]:
                    pv = 0
                else:
                    pv = 1
                pix[stitch, row] = pv
        img.save(outfilename)
        return




class KnitFile():
    """
    Encapsulate the data contained in a Brother knitting machine data set.

    This is designed using a Brother KH930 machine, and may not apply to other machines

    Each saved set of data is two sectors on a brother floppy disk, each conatining 1024 bytes.

    This class is designed to use the same file format used by the PDDemulate disk emulator,
    and access those files on the emulator host. The track numbers used by this class are those
    used when accessing the external floppy on the knitting machine front panel and NOT the physical
    track numbers on the floppy disk or emulator. They map like this:
    Track  1 == Floppy physical tracks 0 and 1
    Track  2 == Floppy physical tracks 2 and 3
    . . .
    Track 40 == Floppy physical tracks 78 and 79
    """

    def __init__(self):
        self.verbose = False
        self.data = None
        self.patterns = {}
        self.tracknum = None
        return

    def __del__(self):
        return

    def open(self, rootdir, tracknum):
        """
        Open the specified track number under the specified directory.
        Track numbers are 1-40
        """
        self.tracknum = tracknum
        psn1 = (tracknum-1)*2
        psn2 = psn1+1
        fn1 = "%02d.dat" % psn1
        fn2 = "%02d.dat" % psn2
        if self.verbose:
            print "reading file %s" % fn1
            print "reading file %s" % fn2

        Fdata = open(os.path.join(rootdir, fn1), 'r')
        data = Fdata.read()
        if len(data) != 1024:
            raise IOError("Wrong file length for %s" % fn1)
        self.data = str(data)
        Fdata.close()

        Fdata = open(os.path.join(rootdir, fn2), 'r')
        data = Fdata.read()
        if len(data) != 1024:
            raise IOError("Wrong file length for %s" % fn1)
        self.data += str(data)
        Fdata.close()
        return

    def patternList(self):
        return self.patterns.keys().sort()

    def dump(self):
        print dump(self.data)
        return

    def dumpPatternMetaInfo(self):
        for pn in self.patterns:
            print "%d" % pn
            for key in self.patterns[pn]:
                if (key == "rowdata") or (key == "memodata"):
                    continue
                print "    %s: %s" % (key, self.patterns[pn][key])

    def __getIndexedNibble(self, offset, nibble):
        """
        Accepts an offset into the data and 
        a nibble index. Nibble index is subtracted
        from the offset (into lower addresses) and
        the indexed nibble is returned
        """
        # nibbles is zero based
        bytes = nibble/2
        m, l = nibbles(self.data[offset-bytes])
        if nibble % 2:
            return m
        else:
            return l

    def __getRowData(self, pattOffset, stitches, rownumber):
        """
        Given an offset into the file, the pattern width
        and the row number, returns an array with the row data
        """
        row=array.array('B')
        nibspr = nibblesPerRow(stitches)
        startnib = nibspr * rownumber
        endnib = startnib + nibspr

        for i in range(startnib, endnib, 1):
            nib = self.__getIndexedNibble(pattOffset, i)
            row.append(nib & 0x01)
            stitches = stitches - 1
            if stitches:
                row.append((nib & 0x02) >> 1)
                stitches = stitches - 1
            if stitches:
                row.append((nib & 0x04) >> 2)
                stitches = stitches - 1
            if stitches:
                row.append((nib & 0x08) >> 3)
                stitches = stitches - 1
        return row

    def readPatterns(self):
        """
        Get a list of custom patterns stored in the file
        Pattern information is stored at the beginning
        of the file, with seven bytes per pattern and
        99 possible patterns, numbered 901-999.
        Returns: A list of tuples:
          patternNumber
          stitches
          rows
          patternOffset
          memoOffset
        """
        idx = 0
        pptr = ADDR_INITIAL_PATTERN_OFFSET
        for pi in range(1, 100):
            flag = ord(self.data[idx])
            if self.verbose:
                print 'Entry %d, flag is 0x%02X' % (pi, flag)
            idx = idx + 1
            unknown = ord(self.data[idx])
            idx = idx + 1
            rh, rt = nibbles(self.data[idx])
            idx = idx + 1
            ro, sh = nibbles(self.data[idx])
            idx = idx + 1
            st, so = nibbles(self.data[idx])
            idx = idx + 1
            unk, ph = nibbles(self.data[idx])
            idx = idx + 1
            pt, po = nibbles(self.data[idx])
            idx = idx + 1
            rows = hto(rh,rt,ro)
            stitches = hto(sh,st,so)
            patno = hto(ph,pt,po)

            # we have this entry
            if self.verbose:
                print '   Pattern %3d: %3d Rows, %3d Stitches - ' % (patno, rows, stitches)
                print 'Unk = %d, Unknown = 0x%02X (%d)' % (unk, unknown, unknown)
            if flag == 0:
                # Not a valid entry, so quit
                break

            # valid entry
            memoff = pptr
            patoff = pptr -  bytesForMemo(rows)
            pptr = pptr - bytesPerPatternAndMemo(stitches, rows)
            if self.verbose:
                 print "Ending offset ", hex(pptr)
            # TODO figure out how to calculate pattern length
            #pptr = pptr - something
            
            # get the row data
            rowdata = []
            for i in range(0, rows):
                arow = self.__getRowData(patoff, stitches, i)
                rowdata.append(arow)

            # get the memo data
            memodata = []
            for i in range(rows):
                memodata.append(self.data[memoff-i])

            self.patterns[patno] = {'stitches':stitches, 'rows':rows, 'memo':memoff, 'offset':patoff, 'pattend':pptr, 'unk_nibble':unk, 'unknown':unknown,
                                    'rowdata':rowdata, 'memodata':memodata, 'changed':False}
        return

    def clearFileData(self):
        """
        Clear the file memory as if a memory clear has been performed on
        the knitting machine (code 888)
        """
        self.data = []
        for i in range(2048):
            self.data.append(char(0))
        # make this look like cleared memory in the KM
        self.data[0x0005] = 0x09
        self.data[0x0006] = 0x01
        self.data[0x0700] = 0x01
        self.data[0x0701] = 0x20
        self.data[0x0710] = 0x07
        self.data[0x0711] = 0xF9
        self.data[0x07EA] = 0x10
        return

    def addPattern(self, patternNumber, rowdata, memodata):
        """
        Add a pattern to pattern memory. If the pattern number is an existing pattern,
        the existing pattern is replaced. If there is not enough room for the new pattern,
        an exception is raised
        """
        return

    def rmPattern(self, patternNumber):
        """
        Delete the specified pattern
        """
        # This should model behavior of the knitting machine delete
        return

    def getFreeRows(self, numStitches):
        """
        Return the approximate number of rows which may be added, if each row
        contiains the specified number of stitches
        """
        return

    def write(self):
        """
        Write the data back to the file(s)
        """
        psn1 = (self.tracknum-1)*2
        psn2 = psn1+1
        fn1 = "%02d.dat" % psn1
        fn2 = "%02d.dat" % psn2

        Fdata = open(os.path.join(rootdir, fn1), 'w')
        Fdata.write(data[:1024])
        Fdata.close()

        Fdata = open(os.path.join(rootdir, fn2), 'w')
        Fdata.write(data[1024:])
        Fdata.close()

        return

    def close(self):
        self.tracknum = None
        return


    def exportCDLData(self, offsetList, outfilename, annotate=False, exclude = ['pattern', 'needle', 'motif']):
        """
        This method exports a comma-delimited list of all the
        byte offsets in the list. It is used for comparing multiple
        knitting machine data files in order to help visualize changes
        between them. If annotate is true, a field is added first which
        contains a mnemonic for known file offsets in the list.
        Does not include pattern data.
        """

        csvf = open(outfilename, 'wt')

        try:
            writer = csv.writer(csvf, quoting=csv.QUOTE_ALL)
            for offset in offsetList:
                # see if we need to skip this offset
                skip = False
                for name in exclude:
                    if name in mem_ranges:
                        if offset in mem_ranges[name]:
                            skip = True
                            break
                if skip:
                    continue
    
                value = self.data[offset]
                if offset in mem_map:
                    descr = mem_map[offset]
                else:
                    descr = "Unknown"
                if annotate:
                    writer.writerow((descr, "0x%04X" % offset ,"0x%02X" % ord(value)))
                else:
                    writer.writerow(( "0x%04X" % offset ,"0x%02X" % ord(value)))
        finally:
            csvf.close()

        return
