# Using the Knitting Machine Tool — KH-930/940 Instructions

These instructions cover the physical steps you need to perform on the
Brother KH-930/940 while using this tool. The software side (uploading an
image, adjusting the threshold, clicking Send) is covered in the UI itself.

---

## Before you begin

1. **Turn the KH-930/940 power switch OFF** before connecting the USB-to-serial
   cable. The receptacle is on the back of the machine toward the right.
2. Plug in the cable with the key matching the notch in the receptacle.
   Make sure the connector is centered horizontally, covering all 8 pins.
3. **Turn the machine on.**

---

## Sending a pattern

### In the tool

1. Upload your image and adjust the threshold until the preview looks right.
2. Set the pattern number (default 901) and click **Write Pattern**.
3. Click **Send to Machine**. The tool will start the PDD emulator and wait
   for the machine to request data.

### On the machine

4. Enter the following key sequence on the KH-930/940 keypad:

   ```
   CE  551  STEP  1  STEP
   ```

   - **CE** puts the machine into download mode.
   - **551** is the floppy drive command.
   - **STEP 1 STEP** requests track 1.

5. The machine will begin reading from the tool. This takes **a few seconds**
   on the KH-930 — the machine holds only about 2 KB of pattern memory
   (~13,000 stitches), so transfer is fast. It will take longer on the KH-940.

6. When the transfer is complete, the machine will **beep** and show the
   green **READY** light with **1** in the display (row 1).

   > If you see a flashing error instead, turn the machine off and check
   > that the cable is properly seated. Turn it back on and try again —
   > you do not need to re-upload the image, just click Send again.

### After transfer

7. Your pattern is now loaded and centered on the needle bed. The tool
   automatically uses Selector 2 and centers the pattern, which is the
   right default for a single motif.

   If you want to change the position on the needle bed or switch to
   Selector 1 for all-over patterning, use the normal pattern-selecting
   process on the machine. See **Selectors, page 21** of the KH-930/940
   Instruction Book.

---

## Knitting the pattern

8. Proceed to knit as usual. Activate pattern knitting at the point in
   your work where you want the pattern to appear, as described in the
   Brother manual.

   A few things to keep in mind:
   - **Dark pixels** knit in the contrast color; **light pixels** in the
     background color.
   - The pattern knits **from the bottom up**, and is **mirrored
     left-to-right** as seen from your side of the machine — both are
     normal for this type of machine.

---

## If your pattern spans more than one track

The KH-930E holds approximately 13,000 stitches. Large or tall patterns
may be split across multiple tracks automatically.

When you are approaching the end of a track:

9. **Listen for the beep** — this signals that the carriage is about to
   knit the second-to-last row of the track.
10. Knit that second-to-last row, then **STOP**.

    > Do not knit the last row yet. If you continue without stopping, the
    > machine will loop back to the beginning of the same track.

11. With the machine paused, enter on the keypad:

    ```
    CE  551  STEP  2  STEP
    ```

    (Replace `2` with `3`, `4`, etc. for subsequent tracks.)

12. The tool will send the next track. When the machine beeps and shows
    READY, knit the last row of the previous track and continue.

    > Make sure the carriage passes **outside the turn mark** before the
    > last row, and that your computer has not gone to sleep — a sleeping
    > computer cannot respond to the machine's data request.

---

## Disconnecting

- It is safe to unplug the USB end of the cable from the computer at any
  time **except during an active transfer**.
- Before unplugging the cable from the **machine** side, turn the KH-930/940
  off first, as instructed in the Brother manual for the FB-100 drive.
- The machine retains its loaded pattern after power-off. You can turn it
  off and resume later without re-sending.
