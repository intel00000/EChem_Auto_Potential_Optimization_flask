# pyserial imports
import serial
import serial.tools.list_ports

# other library
import re
import json
import logging
from queue import Queue
from datetime import datetime

# Custom imports
from Message import simple_Message


class AutosamplerController:
    def __init__(self, controller_id: int, port_name: str, serial_timeout: int):
        self.serial_port = serial.Serial()  # Init the serial port but don't open it yet
        self.serial_port.port = port_name
        self.serial_port.baudrate = 115200
        self.serial_port.timeout = serial_timeout

        # A queue to store commands to be sent to the autosampler controller
        self.send_command_queue = Queue()

        # Dictionary to store status of this autosampler controller
        self.status = {
            "serial_port": self.serial_port.name,
            "controller_id": controller_id,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "connected": False,
            "slots": [],
            "rtc_time": -1,
        }
        logging.info(f"Autosampler controller {controller_id} created.")

    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def connect(self) -> simple_Message:
        """Connect to the serial port."""
        if self.is_connected():
            self.disconnect()
        try:
            self.serial_port.open()
            self.serial_port.reset_input_buffer()  # Flush the input and output buffers
            self.serial_port.reset_output_buffer()
            self.serial_port.write("0:ping\n".encode())  # Identify Pico type
            response = self.serial_port.readline().decode("utf-8").strip()
            if "Pico Autosampler Control Version" not in response:
                self.disconnect()  # Wrong device
                return simple_Message(
                    "Error", "Connected to the wrong device. Please reconnect."
                )
            self.sync_rtc_with_pc_time()  # Sync RTC with PC time
            self.query_config()  # Query slots info
            logging.info(f"Connected to {self.serial_port.name}")
            self.status.update({"connected": True})
            return simple_Message("Success", f"Connected to {self.serial_port.name}")
        except Exception as e:
            # attempt to disconnect if connection fails
            self.disconnect()
            logging.error(f"Failed to connect to {self.serial_port.name}: {e}")
            return simple_Message(
                "Error", f"Failed to connect to {self.serial_port.name}: {e}"
            )

    def disconnect(self) -> simple_Message:
        """Disconnect from the serial port."""
        try:
            if self.is_connected():
                self.serial_port.close()
                self.send_command_queue.queue.clear()
                logging.info(f"Disconnected from {self.serial_port.name}")
                self.status.update({"connected": False, "slots": [], "rtc_time": -1})
                return simple_Message(
                    "Success", f"Disconnected from {self.serial_port.name}"
                )
        except Exception as e:
            logging.error(f"Failed to disconnect from {self.serial_port.name}: {e}")
            return simple_Message(
                "Error", f"Failed to disconnect from {self.serial_port.name}: {e}"
            )

    def send_command(self) -> None:
        try:
            if self.is_connected() and not self.send_command_queue.empty():
                command = self.send_command_queue.get(block=False)
                self.serial_port.write(f"{command.strip()}\n".encode())
                # don't log the RTC time sync command
                if "time" not in command:
                    logging.debug(f"PC -> Pico: {command}")
        except serial.SerialException as e:
            self.disconnect()
            logging.error(f"Error: disconnecting from {self.serial_port.name}: {e}")
        except serial.SerialTimeoutException as e:
            self.disconnect()
            logging.error(f"Timeout error: {e}")
            return simple_Message("Error", f"Timeout occurred: {e}")
        except Exception as e:
            self.disconnect()
            logging.error(f"Error: {e}")

    def read_serial(self, wait=False) -> simple_Message:
        try:
            if self.is_connected() and (self.serial_port.in_waiting or wait):
                response = self.serial_port.readline().decode("utf-8").strip()
                if "RTC Time" not in response:
                    logging.debug(f"Autosampler -> PC: {response}")
                if "Autosampler Configuration:" in response:
                    self.parse_config(response)
                elif "RTC Time" in response:
                    self.parse_rtc_time(response)
                elif "Success" in response:
                    return simple_Message("Success", response)
                elif "Error" in response:
                    return simple_Message("Error", response)
        except serial.SerialException as e:
            self.disconnect()
            logging.error(f"Error: {e}")
            return simple_Message(
                "Error",
                f"Connection to Pico lost. Please reconnect to continue.",
            )
        except Exception as e:
            self.disconnect()
            logging.error(f"Error reading serial: {e}")
            return simple_Message("Error", f"An error occurred: {e}")
        return simple_Message("", "")

    def sync_rtc_with_pc_time(self) -> None:
        """Synchronize the Pico's RTC with the PC's time."""
        try:
            now = datetime.now()
            sync_command = f"0:stime:{now.year}:{now.month}:{now.day}:{now.hour}:{now.minute}:{now.second}"
            self.serial_port.write(f"{sync_command}\n".encode())
        except Exception as e:
            logging.error(f"Error syncing RTC: {e}")

    def query_rtc_time(self) -> None:
        if self.is_connected():
            try:
                self.send_command_queue.put("0:time")
            except Exception as e:
                logging.error(f"Error: {e}")
                return simple_Message("Error", f"An error occurred: {e}")

    def query_config(self) -> None:
        """Query the slots configuration from the Pico."""
        if self.is_connected():
            try:
                self.send_command_queue.put("config")
                response = self.serial_port.readline().decode("utf-8").strip()
                self.parse_config(response)
            except Exception as e:
                logging.error(f"Error querying configuration: {e}")

    def parse_rtc_time(self, response) -> None:
        try:
            match = re.search(r"RTC Time: (\d+-\d+-\d+ \d+:\d+:\d+)", response)
            rtc_time = match.group(1)
            if rtc_time:
                self.status["rtc_time"] = datetime.strptime(
                    rtc_time, "%Y-%m-%d %H:%M:%S"
                ).timestamp()
        except Exception as e:
            logging.error(f"Error updating RTC time display: {e}")

    def parse_config(self, response) -> None:
        try:
            config_str = response.replace("Autosampler Configuration:", "").strip()
            autosampler_config = json.loads(config_str)
            self.status["slots"] = list(autosampler_config.keys())
            self.status["slots"].sort()
            logging.info(f"Slots populated: {self.status['slots']}")
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding configuration: {e}")

    def goto_position(self, position: str) -> simple_Message:
        if self.is_connected():
            try:
                if position.isdigit():
                    self.send_command_queue.put(f"position:{position}")
                    logging.info(f"Going to position command sent: {position}")
                    return simple_Message("Success", f"Moving to position command sent: {position}")
                else:
                    return simple_Message("Error", "Invalid position input.")
            except Exception as e:
                logging.error(f"Error going to position: {e}")
                return simple_Message("Error", f"An error occurred: {e}")

    def goto_slot(self, slot: str) -> simple_Message:
        if self.is_connected():
            try:
                if slot in self.status["slots"]:
                    self.send_command_queue.put(f"slot:{slot}")
                    logging.info(f"Going to slot command sent: {slot}")
                    return simple_Message("Success", f"Moving to slot command sent: {slot}")
                else:
                    return simple_Message("Error", "Invalid slot input.")
            except Exception as e:
                logging.error(f"Error going to slot: {e}")
                return simple_Message("Error", f"An error occurred: {e}")
