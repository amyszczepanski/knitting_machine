"""I asked the internet's robots to write some code to start with"""

import serial
import time
import threading

ACK_TIMEOUT = 5.0  # seconds


class KnittingController:
    """
    A controller for a Brother knitting machine.

    Handles all serial communication and maintains the machine state.
    This class is designed to run in the background, allowing a UI or web API
    to interact with it without blocking.
    """

    def __init__(self, port="/dev/ttyUSB0", baudrate=9600):
        """
        Initialize the controller.

        Args:
            port (str): Serial port the knitting machine is connected to.
            baudrate (int): Serial baud rate (default 9600).
        """
        self.port = port
        self.baudrate = baudrate
        self.serial = None

        self._running = False
        self._thread = None

        # State machine variables
        self._state = "IDLE"
        self._pattern = []
        self._pattern_index = 0
        self._last_send_time = 0
        self._stop_requested = False

        self._max_retries = 3  # number of attempts per row
        self._current_retry = 0  # current attempt for this row

        # Observable state for UI / web
        self.state = {
            "connected": False,
            "running": False,
            "progress": 0.0,
            "last_error": None,
        }

    def connect(self):
        """
        Connect to the knitting machine over the serial port.

        Updates state["connected"] and state["last_error"].
        If connection fails, the controller remains in a safe disconnected state.
        """
        try:
            self.serial = serial.Serial(self.port, self.baudrate, timeout=0.1)
            self.state["connected"] = True
            self.state["last_error"] = None  # clear previous errors
        except serial.SerialException as e:
            self.serial = None
            self.state["connected"] = False
            self.state["last_error"] = f"Failed to connect: {e}"

    def disconnect(self):
        """
        Disconnect from the knitting machine safely.

        If an error occurs during disconnect, updates state["last_error"] but
        ensures the controller ends up in a safe state.
        """
        try:
            if self.serial and self.serial.is_open:
                self.serial.close()
        except serial.SerialException as e:
            self.state["last_error"] = f"Error closing serial port: {e}"
        finally:
            self.serial = None
            self.state["connected"] = False

    def start(self, pattern):
        """
        Start sending a knitting pattern to the machine.

        This starts a background thread that repeatedly calls `_tick()`.
        The controller enters the "SENDING_PATTERN" state.

        Args:
            pattern (list[bytes]): List of rows/commands to send to the machine.
        """
        if self._running:
            return
        self._pattern = pattern
        self._pattern_index = 0
        self._state = "SENDING_PATTERN"
        self._stop_requested = False

        self._running = True
        self.state["running"] = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """
        Request the controller to stop the current operation.

        The controller will finish the current tick and then stop safely.
        """
        self._stop_requested = True

    def _run_loop(self):
        """
        Background loop that repeatedly calls `_tick()`.

        Runs in a separate daemon thread when `start()` is called.
        """
        while self._running:
            self._tick()
            time.sleep(0.01)  # 10ms per tick

    def _tick(self):
        """
        Advance the knitting machine by one step.

        This method should be called repeatedly by the background thread.
        It:
            - Dispatches based on the current state
            - Handles stop requests
            - Updates machine state and progress
            - Does not block or interact with the UI

        Internal state variables:
            _state: Current machine state
            _pattern_index: Index of the next row to send
            _last_send_time: Time of last serial send
        """
        if not self.serial:
            return

        if self._stop_requested:
            self._handle_stop()
            return

        if self._state == "ERROR":
            self._handle_error()
            return

        if self._state == "IDLE":
            return
        elif self._state == "SENDING_PATTERN":
            self._send_pattern_step()
        elif self._state == "WAITING_FOR_READY":
            self._check_ready_signal()

    def _send_pattern_step(self):
        """
        Send the next row/command of the pattern to the machine with retry logic.

        - Attempts up to `_max_retries` times if ACK is not received.
        - Updates state to WAITING_FOR_READY after sending.
        - If retries are exhausted, sets state to ERROR.
        """
        if self._pattern_index >= len(self._pattern):
            # Finished pattern
            self._state = "IDLE"
            self._running = False
            self.state["running"] = False
            return

        row = self._pattern[self._pattern_index]

        try:
            if self.serial is None or not self.serial.is_open:
                raise serial.SerialException("Serial port not connected")

            self.serial.write(row)
            self._last_send_time = time.monotonic()
            self._state = "WAITING_FOR_READY"
            self.state["last_error"] = None  # clear previous error

        except serial.SerialException as e:
            # Handle serial write failure
            self._current_retry += 1
            if self._current_retry > self._max_retries:
                self._state = "ERROR"
                self.state["last_error"] = (
                    f"Failed to send row {self._pattern_index} after {self._max_retries} attempts: {e}"
                )
            else:
                # Try again on next tick
                self.state["last_error"] = (
                    f"Retry {self._current_retry}/{self._max_retries} for row {self._pattern_index}"
                )

    def _check_ready_signal(self):
        """
        Check the machine's serial input for an acknowledgment (ACK).

        - If ACK received: increment pattern index and continue sending.
        - If timeout expires without ACK: set state to ERROR.
        - If unexpected byte or serial error: set state to ERROR.
        """
        try:
            if self.serial is None or not self.serial.is_open:
                raise serial.SerialException("Serial port not connected")

            data = self.serial.read(1)

            # ACK received
            if data == b"\x06":  # ACK
                self._pattern_index += 1
                self._current_retry = 0  # reset retries for next row
                self.state["progress"] = self._pattern_index / len(self._pattern)
                self._state = "SENDING_PATTERN"
                self.state["last_error"] = None

            # Unexpected byte
            elif data:
                self._state = "ERROR"
                self.state["last_error"] = f"Unexpected byte from machine: {data}"

            # No data yet — check timeout
            elif time.monotonic() - self._last_send_time > self.ACK_TIMEOUT:
                self._state = "ERROR"
                self.state["last_error"] = (
                    f"Timeout waiting for ACK for row {self._pattern_index}"
                )

            # else: still waiting, just return

        except serial.SerialException as e:
            self._state = "ERROR"
            self.state["last_error"] = f"Serial error while reading ACK: {e}"

    def _handle_stop(self):
        """
        Stop the controller safely.

        Ensures _running, state["running"], and _state are set correctly.
        """
        self._running = False
        self.state["running"] = False
        self._state = "IDLE"
        self.state["last_error"] = None  # clear any transient errors on stop

    def _handle_error(self):
        """
        Handle an error condition safely.

        Logs the last_error (if needed) and stops the controller.
        Ensures the controller remains in a consistent, safe state.
        """
        print("Controller encountered an error:", self.state.get("last_error"))
        self._running = False
        self.state["running"] = False
        self._state = "IDLE"
