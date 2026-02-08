#!/usr/bin/python
# -*- coding: UTF-8 -*-

from .Config import Config
from .Messages import Messages
from app.gui.Gui import Gui
from PDDemulate import PDDemulator
from PDDemulate import PDDEmulatorListener
from dumppattern import PatternDumper
from insertpattern import PatternInserter
import tkinter
from tkinter import filedialog
import os
import os.path
from PIL import Image


class KnittingApp(tkinter.Tk):

    def __init__(self, parent=None):
        tkinter.Tk.__init__(self, parent)
        self.parent = parent
        self.initialize()
        # self.startEmulator()

    def initialize(self):
        self.msg = Messages(self)
        self.patterns = []
        self.pattern = None
        self.currentDatFile = None

        self.initConfig()
        self.initializeUtilities()

        self.gui = Gui()
        self.gui.initializeMainWindow(self)
        self.updatePatternCanvasLastSize()
        self.patternListBox.bind("<<ListboxSelect>>", self.patternSelected)
        self.after_idle(self.canvasConfigured)
        self.deviceEntry.entryText.set(self.getConfig().device)
        self.datFileEntry.entryText.set(self.getConfig().datFile)

        self.initEmulator()
        self.after_idle(self.reloadPatternFile)

    def initializeUtilities(self):
        self.patternDumper = PatternDumper()
        self.patternDumper.printInfoCallback = self.msg.showInfo
        self.patternInserter = PatternInserter()
        self.patternInserter.printInfoCallback = self.msg.showInfo
        self.patternInserter.printErrorCallback = self.msg.showError

    def initEmulator(self):
        self.emu = PDDemulator(self.getConfig().imgdir)
        self.emu.listeners.append(PDDListener(self))
        # self.emu = lambda: 1
        self.setEmulatorStarted(False)

    def emuButtonClicked(self):
        self.getConfig().device = self.deviceEntry.entryText.get()
        if self.emu.started:
            self.stopEmulator()
        else:
            self.startEmulator()

    def startEmulator(self):
        self.msg.showInfo("Preparing emulator. . . Please Wait")

        if self.getConfig().simulateEmulator:
            self.msg.showInfo("Simulating emulator, emulator is not started...")
            self.setEmulatorStarted(True)
        else:
            try:
                port = self.getConfig().device
                self.emu.open(cport=port)
                self.msg.showInfo("PDDemulate Version 1.1 Ready!")
                self.setEmulatorStarted(True)
                self.after_idle(self.emulatorLoop)
            except Exception as e:
                self.msg.showError(
                    "Ensure that TFDI cable is connected to port "
                    + port
                    + "\n\nError: "
                    + str(e)
                )
                self.setEmulatorStarted(False)

    def emulatorLoop(self):
        if self.emu.started:
            self.emu.handleRequest(False)
            # repeated call to after_idle() caused all window dialogs to hang out application, using after() each 10 milliseconds
            self.after(100, self.emulatorLoop)

    def stopEmulator(self):
        if self.emu is not None:
            self.emu.close()
            self.msg.showInfo("PDDemulate stopped.")
            self.setEmulatorStarted(False)
        self.initEmulator()

    def quitApplication(self):
        self.stopEmulator()
        self.after_idle(self.quit)

    def setEmulatorStarted(self, started):
        self.emu.started = started
        if started:
            self.gui.setEmuButtonStarted()
        else:
            self.gui.setEmuButtonStopped()

    def getConfig(self):
        return self.config

    def initConfig(self):
        cfg = Config()
        if not hasattr(cfg, "device"):
            cfg.device = ""
        if not hasattr(cfg, "datFile"):
            cfg.datFile = ""
        if not hasattr(cfg, "simulateEmulator"):
            cfg.simulateEmulator = False
        self.config = cfg

    def reloadPatternFile(self, pathToFile=None):
        if not pathToFile:
            pathToFile = self.datFileEntry.entryText.get()
        else:
            self.datFileEntry.entryText.set(pathToFile)

        if not pathToFile:
            return
        self.currentDatFile = pathToFile
        try:
            result = self.patternDumper.dumppattern([pathToFile])
            self.patterns = result.patterns
            listBoxModel = []
            for p in self.patterns:
                listBoxModel.append(self.getPatternTitle(p))
            selectedIndex = self.getSelectedPatternIndex()
            self.patternListBox.items.set(listBoxModel)
            self.setSelectedPatternIndex(selectedIndex)
        except IOError as e:
            self.msg.showError(
                "Could not open pattern file %s" % pathToFile + "\n" + str(e)
            )

    def storeTrack(self, pathToFile=None):
        if not pathToFile:
            pathToFile = self.datFileEntry.entryText.get()
        self.msg.showInfo("Storing tracks for file " + pathToFile)
        trackFile1, trackFile2 = "00.dat", "01.dat"
        trackPath1 = os.path.join(self.config.imgdir, trackFile1)
        trackPath2 = os.path.join(self.config.imgdir, trackFile2)
        trackSize = 1024

        startEmu = self.emu.started
        if startEmu:
            self.stopEmulator()

        infile = track0file = track1file = None

        infile = open(pathToFile, "rb")

        try:
            track0file = open(trackPath1, "wb")
            track1file = open(trackPath2, "wb")

            t0dat = infile.read(trackSize)
            t1dat = infile.read(trackSize)

            track0file.write(t0dat)
            track1file.write(t1dat)
            self.msg.showInfo(
                "Stored file to tracks "
                + trackFile1
                + " and "
                + trackFile2
                + " in "
                + self.config.imgdir
            )
        except Exception as e:
            self.msg.showError(str(e))
        finally:
            if infile:
                self.msg.showDebug("Closing infile...")
                infile.close
            if track0file:
                self.msg.showDebug("Closing track0file...")
                track0file.close()
            if track1file:
                self.msg.showDebug("Closing track1file...")
                track1file.close()
            if startEmu:
                self.startEmulator()

    def helpButtonClicked(self):
        helpMsg = """Commands to execute on Knitting machine:

552: Download patterns from machine to computer
551: Upload patterns from computer to machine
     - before this, make sure that you stored file to track
     - afterfards pressing 551, press 1 to load track with inserted patterns
"""
        self.msg.showMoreInfo(helpMsg)

    def reloadDatFileButtonClicked(self):
        self.reloadPatternFile()

    def storeTrackButtonClicked(self):
        self.storeTrack()

    def chooseDatFileButtonClicked(self):
        filePath = filedialog.askopenfilename(
            filetypes=[("DAT file", "*.dat")],
            initialfile=self.datFileEntry.entryText.get(),
            title="Choose dat file with patterns...",
        )
        if len(filePath) > 0:
            self.msg.showInfo("Opened dat file " + filePath)
            self.reloadPatternFile(filePath)

    def patternSelected(self, evt):
        # w = evt.widget
        index = self.getSelectedPatternIndex()
        if index is not None:
            pattern = self.patterns[index]
        else:
            pattern = None
        self.displayPattern(pattern)

    def getSelectedPatternIndex(self):
        sel = self.patternListBox.curselection()
        if len(sel) > 0:
            return int(sel[0])
        else:
            return None

    def setSelectedPatternIndex(self, index):
        lb = self.patternListBox
        if index is None:
            index = 0
        if lb.size() == 0:
            self.displayPattern(None)
            return
        if index > lb.size():
            index = 0
        self.patternListBox.selection_set(index)
        self.displayPattern(self.patterns[index])

    def displayPattern(self, pattern=None):
        if not pattern:
            pattern = self.pattern
        self.patternCanvas.clear()
        self.patternTitle.caption.set(self.getPatternTitle(pattern))
        if pattern:
            result = self.patternDumper.dumppattern(
                [self.currentDatFile, str(pattern["number"])]
            )
            self.printPatternOnCanvas(result.pattern)
        self.pattern = pattern

    def getPatternTitle(self, pattern):
        p = pattern
        if p:
            return (
                "Pattern no: "
                + str(p["number"])
                + " (rows x stitches: "
                + str(p["rows"])
                + " x "
                + str(p["stitches"])
                + ")"
            )
        else:
            return "No pattern"

    def printPatternOnCanvas(self, pattern):
        #        pattern = []
        #        for x in range(8):
        #            row = []
        #            for y in range(13):
        #                row.append((y % 2 + x % 2) % 2)
        #            pattern.append(row)
        patternHeight = len(pattern)
        patternWidth = len(pattern[0])
        marginx, marginy = 10, 10
        bitWidth = (self.patternCanvas.getWidth() - marginx) / (patternWidth)
        bitHeight = (self.patternCanvas.getHeight() - marginy) / (patternHeight)
        bitWidth = min(bitWidth, bitHeight)
        bitHeight = bitWidth
        self._printPatternBody(pattern, marginx, marginy, bitWidth, bitHeight)
        secCoordbig, secCoordsmall, secCoord2 = 0, marginy / 2, marginy
        step, bigStep = 5, 10
        for i in range(0, max(patternWidth, patternHeight) + 1, step):
            secCoord = secCoordbig if i % bigStep == 0 else secCoordsmall
            if i < patternWidth:
                xCoord = marginx + i * bitWidth
                self.patternCanvas.create_line(xCoord, secCoord, xCoord, secCoord2)
            if i < patternHeight:
                yCoord = marginx + i * bitHeight
                self.patternCanvas.create_line(secCoord, yCoord, secCoord2, yCoord)

    def _printPatternBody(self, pattern, patternPosx, patternPosy, bitWidth, bitHeight):
        patternHeight = len(pattern)
        patternWidth = len(pattern[0])
        self.patternCanvas.clear()
        for row in range(patternHeight):
            for stitch in range(patternWidth):
                if (pattern[row][stitch]) == 1:
                    fill = "black"
                    border = "white"
                    # border=fill
                else:
                    fill = "white"
                    border = "black"
                    # border=fill
                row = patternHeight - row - 1
                # stitch = patternWidth - stitch - 1
                self.patternCanvas.create_rectangle(
                    patternPosx + stitch * bitWidth,
                    patternPosy + row * bitHeight,
                    patternPosx + (stitch + 1) * bitWidth,
                    patternPosy + (row + 1) * bitHeight,
                    width=1,
                    fill=fill,
                    outline=border,
                )
        # pattern border
        self.patternCanvas.create_rectangle(
            patternPosx,
            patternPosy,
            patternPosx + (patternWidth) * bitWidth,
            patternPosy + (patternHeight) * bitHeight,
            width=1,
            outline="black",
        )

    def updatePatternCanvasLastSize(self):
        self.patternCanvas.lastWidth = self.patternCanvas.getWidth()
        self.patternCanvas.lastHeight = self.patternCanvas.getHeight()

    def canvasConfigured(self):
        if (
            self.patternCanvas.lastWidth != self.patternCanvas.getWidth()
            or self.patternCanvas.lastHeight != self.patternCanvas.getHeight()
        ):
            self.msg.displayMessages = False
            self.updatePatternCanvasLastSize()
            self.displayPattern()
            self.msg.displayMessages = True
        self.after(100, self.canvasConfigured)

    def insertBitmapButtonClicked(self):
        sel = self.patternListBox.curselection()
        if len(sel) == 0:
            self.msg.showError("Target pattern for insertion must be selected!")
            return
        index = int(sel[0])
        pattern = self.patterns[index]
        filePath = filedialog.askopenfilename(
            filetypes=[("2-color Bitmap", "*.bmp")],
            title="Choose bitmap file to insert...",
        )
        if len(filePath) > 0:
            self.insertBitmap(filePath, pattern["number"])

    def exportBitmapButtonClicked(self):
        sel = self.patternListBox.curselection()
        if len(sel) == 0:
            self.msg.showError("Target pattern for saving must be selected!")
            return
        index = int(sel[0])
        pattern = self.patterns[index]
        filePath = filedialog.asksaveasfilename(
            filetypes=[("2-color Bitmap", "*.bmp")], title="Save as a bitmap file..."
        )
        if len(filePath) > 0:
            patternNumber = pattern["number"]
            self.msg.showInfo(
                "Saving pattern number %d as bmp file %s" % (patternNumber, filePath)
            )
            result = self.patternDumper.dumppattern(
                [self.currentDatFile, str(patternNumber)]
            )
            pattern = result.pattern
            patternHeight = len(pattern)
            patternWidth = len(pattern[0])
            img = Image.new("RGB", (patternWidth, patternHeight), None)
            for x in range(patternWidth):
                for y in range(patternHeight):
                    color = (
                        (0, 0, 0)
                        if pattern[patternHeight - y - 1][x] == 1
                        else (255, 255, 255)
                    )
                    img.putpixel((x, y), color)
            img = img.convert("1")
            img.save(filePath, "BMP")
            self.msg.showInfo(
                "Saved pattern number %d as bmp file %s" % (patternNumber, filePath)
            )

    def insertBitmap(self, bitmapFile, patternNumber):
        self.msg.showInfo(
            "Inserting dat file %s to pattern number %d" % (bitmapFile, patternNumber)
        )
        oldBrotherFile = self.currentDatFile
        self.patternInserter.insertPattern(
            oldBrotherFile, patternNumber, bitmapFile, oldBrotherFile
        )
        self.reloadPatternFile()


class PDDListener(PDDEmulatorListener):

    def __init__(self, app):
        self.app = app

    def dataReceived(self, fullFilePath):
        self.app.reloadPatternFile(fullFilePath)


if __name__ == "__main__":
    app = KnittingApp()
    app.mainloop()
