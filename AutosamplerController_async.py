# pyserial imports
import serial
import serial.tools.list_ports

# other library
import re
import json
import asyncio
import logging
from datetime import datetime


class AutosamplerController:
    def __init__(self, controller_id: int, port_name: str, serial_timeout: int):
        self.serial_port = serial.Serial()  # Init the serial port but don't open it yet
        self.serial_port.port = port_name
        self.serial_port.baudrate = 115200
        self.serial_port.timeout = serial_timeout

        # Dictionary to store status of this autosampler controller
        self.status = {
            "serial_port": self.serial_port.name,
            "controller_id": controller_id,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "connected": False,
            "slots": [],
            "slots_configuration": {},
            "position": None,
            "direction": None,
            "rtc_time": -1,
        }
        logging.info(f"Autosampler controller {controller_id} created.")

    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def connect(self) -> str:
        """Connect to the serial port."""
        if self.is_connected():
            self.disconnect()
        try:
            self.serial_port.open()
            # Flush the input and output buffers
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()

            # Identify Pico type
            self.serial_port.write("0:ping\n".encode())
            response = self.serial_port.readline().decode("utf-8").strip()
            if "Pico Autosampler Control Version" not in response:
                self.disconnect()  # Wrong device
                return "Error: Connected to the wrong device."

            # synchronize the time with PC
            now = datetime.now()
            sync_command = f"0:stime:{now.year}:{now.month}:{now.day}:{now.hour}:{now.minute}:{now.second}"
            self.serial_port.write(f"{sync_command}\n".encode())
            response = self.serial_port.readline().decode("utf-8").strip()

            self.query_rtc_time()  # Query time
            self.query_config()  # Query slots information
            self.query_status()  # Query autosampler status
            self.status.update({"connected": True})
            logging.info(f"Connected to {self.serial_port.name}")
            return f"Success: Connected to {self.serial_port.name}"
        except Exception as e:
            # attempt to disconnect if connection fails
            self.disconnect()
            logging.error(f"Failed to connect to {self.serial_port.name}: {e}")
            return f"Error: Failed to connect to {self.serial_port.name}: {e}"

    def disconnect(self) -> str:
        """Disconnect from the serial port."""
        try:
            if self.is_connected():
                self.serial_port.close()  # close the serial port
                logging.info(f"Disconnected from {self.serial_port.name}")
                self.status.update(
                    {
                        "connected": False,
                        "slots": [],
                        "slots_configuration": {},
                        "position": None,
                        "direction": None,
                        "rtc_time": -1,
                    }
                )
                return f"Success: Disconnected from {self.serial_port.name}"
        except Exception as e:
            logging.error(f"Failed to disconnect from {self.serial_port.name}: {e}")
            return f"Error: Failed to disconnect from {self.serial_port.name}: {e}"

    async def send_command(self, command: str) -> str:
        """Send command asynchronously."""
        try:
            if self.is_connected():
                self.serial_port.write(f"{command.strip()}\n".encode())
                # don't log the RTC time sync command
                if "time" not in command:
                    logging.debug(f"PC -> Pico: {command}")
                return f"Success: Command sent: {command}"
        except serial.SerialException as e:
            self.disconnect()
            logging.error(f"Error: SerialException from {self.serial_port.name}: {e}")
            return f"Error: SerialException: {e}"
        except serial.SerialTimeoutException as e:
            self.disconnect()
            logging.error(f"Serial Timeout error: {e}")
            return f"Error: Serial Timeout: {e}"
        except Exception as e:
            logging.error(f"Error: Failed to send command: {e}")
            await self.disconnect()
            return f"Error: Failed to send command: {e}"

    # simply read the serial port and return the response
    # check if specific keyword is in the response, None is returned if not found
    async def read_serial(self, keyword: str = None) -> str:
        """Read serial data asynchronously."""
        response = None
        try:
            if self.is_connected():
                # read the serial port, will block until a response is received
                response = self.serial_port.readline().decode("utf-8").strip()
                if "RTC Time" not in response:  # don't log the RTC time sync response
                    logging.debug(f"Autosampler -> PC: {response}")
                # check if the keyword is in the response
                if keyword and keyword not in response:
                    response = None
        except serial.SerialException as e:
            await self.disconnect()
            logging.error(f"Error: SerialException from {self.serial_port.name}: {e}")
        except serial.SerialTimeoutException as e:
            await self.disconnect()
            logging.error(f"Timeout error: {e}")
        except Exception as e:
            await self.disconnect()
            logging.error(f"Error: {e}")
        finally:
            return response

    async def run_command_and_read(self, command: str, keyword: str, callback):
        """Run send_command and read_serial concurrently."""
        # Use asyncio.gather to run them concurrently
        send_task = self.send_command(command)
        read_task = self.read_serial(keyword)

        # Wait for both tasks to complete
        await asyncio.gather(send_task, read_task)

        # If the response contains the expected keyword, process it with the callback
        response = await read_task
        if response:
            await callback(response)

    async def query_rtc_time(self) -> None:
        """Query the RTC time asynchronously."""
        # Concurrently send the command and read the response
        await self.run_command_and_read("time", "RTC Time", self.parse_rtc_time)

    async def parse_rtc_time(self, response: str) -> None:
        """Parse the RTC time from the response."""
        try:
            # response format: RTC Time: 2024-9-26 11:47:39
            match = re.search(r"RTC Time: (\d+-\d+-\d+ \d+:\d+:\d+)", response)
            rtc_time = match.group(1)
            if rtc_time:  # update the status dictionary
                self.status["rtc_time"] = datetime.strptime(
                    rtc_time, "%Y-%m-%d %H:%M:%S"
                ).timestamp()
        except Exception as e:
            logging.error(f"Error updating RTC time display: {e}")

    def query_config(self) -> None:
        """Query the slots configuration from the Pico."""
        if self.is_connected():
            try:
                self.send_command_queue.put("config")
            except Exception as e:
                logging.error(f"Error querying configuration: {e}")

    def parse_config(self, response: str) -> None:
        """Parse slots configuration from the Pico."""
        try:
            config_str = response.replace("Autosampler Configuration:", "").strip()
            autosampler_config = json.loads(config_str)
            self.status["slots_configuration"] = autosampler_config
            self.status["slots"] = sorted(
                autosampler_config.keys(),
                key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else x.lower()),
            )  # sort in ascending order
            logging.info(f"Slots populated: {self.status['slots']}")
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding configuration: {e}")
        except Exception as e:
            logging.error(f"Error updating slots configuration: {e}")

    def query_status(self) -> None:
        """Query the autosampler status."""
        if self.is_connected():
            try:
                self.send_command_queue.put("status")
            except Exception as e:
                logging.error(f"Error querying status: {e}")

    def parse_status(self, response: str) -> None:
        """Parse autosampler status, including position and direction."""
        try:
            match = re.search(r"position: *(\d+),\s*direction: (Left|Right)", response)
            if match:
                self.status["position"] = int(match.group(1))
                self.status["direction"] = match.group(2)
                logging.info(
                    f"Autosampler status: position {self.status['position']}, direction {self.status['direction']}"
                )
        except Exception as e:
            logging.error(f"Error parsing status: {e}")

    def goto_position(self, position: str) -> None:
        if self.is_connected():
            try:
                if position.isdigit():
                    self.send_command_queue.put(f"position:{position}")
                    logging.info(f"Going to position command sent: {position}")
                else:
                    logging.error("Invalid position input.")
            except Exception as e:
                logging.error(f"Error going to position: {e}")

    def goto_slot(self, slot: str) -> None:
        if self.is_connected():
            try:
                if slot in self.status["slots"]:
                    self.send_command_queue.put(f"slot:{slot}")
                    logging.info(f"Going to slot command sent: {slot}")
                else:
                    logging.error("Invalid slot input.")
            except Exception as e:
                logging.error(f"Error going to slot: {e}")
