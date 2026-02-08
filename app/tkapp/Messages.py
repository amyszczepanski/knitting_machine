import tkinter.messagebox as mb
import sys


class Messages:
    def __init__(self, knittinApp):
        self.app = knittinApp
        self.displayMessages = True
        self.debug = True

    def showError(self, msg):
        if self.displayMessages:
            self.clear()
            sys.stderr.write("Error: " + str(msg) + "\n")
            mb.showerror("Error:", str(msg))

    def showMoreInfo(self, msg):
        if self.displayMessages:
            self.clear()
            print(msg)
            mb.showinfo("Info:", str(msg))

    def showInfo(self, msg):
        if self.displayMessages:
            self.clear()
            print(msg)
            self.app.infoLabel.caption.set("Info: " + str(msg))

    def showDebug(self, msg):
        if self.debug:
            print("DEBUG:" + msg)

    def clear(self):
        if self.displayMessages:
            self.app.infoLabel.caption.set("")
