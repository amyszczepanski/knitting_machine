import os


class Config:
    def __init__(self):
        self.imgdir = "img"
        if os.sys.platform == "win32":
            self.device = "com34"
            #            self.datFile = 'C:/Documents and Settings/ondro/VirtualBox shared folder/knitting/knitting_machine-master/img/zaloha vzory stroj python.dat'
            self.datFile = "C:/Documents and Settings/ondro/VirtualBox shared folder/knitting/knitting_machine-master/file-06.dat"
            self.simulateEmulator = True
        else:
            self.device = "/dev/ttyUSB0"
