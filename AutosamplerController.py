import re
import json
import serial
import asyncio
import logging
from datetime import datetime
from multiprocessing import Lock, Manager


class AutosamplerController:
    def __init__(
        self,
        controller_id: int,
        port_name: str,
        serial_timeout: int,
        lock: Lock,
        manager: Manager,
        logger: logging.Logger,
    ):
        self.serial_port = serial.Serial()  # Init the serial port but don't open it yet
        self.serial_port.port = port_name
        self.serial_port.baudrate = 115200
        self.serial_port.timeout = None
        self.lock = lock  # Lock to ensure safe access to shared dictionary
        self.logger = logger

        # Shared dictionary to store the status
        self.status = manager.dict(
            {
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
        )

        self.logger.info(f"Autosampler controller {controller_id} created.")

    def __is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    async def connect(self) -> str:
        """Connect to the serial port asynchronously."""
        if self.__is_connected():
            await self.disconnect()
        try:
            self.serial_port.open()
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()

            # Identify Pico type
            self.serial_port.write("0:ping\n".encode())
            response = self.serial_port.readline().decode("utf-8").strip()
            if "Pico Autosampler Control Version" not in response:
                await self.disconnect()  # Wrong device
                return "Error: Connected to the wrong device."

            # Synchronize the time with PC
            now = datetime.now()
            sync_command = f"0:stime:{now.year}:{now.month}:{now.day}:{now.hour}:{now.minute}:{now.second}"
            self.serial_port.write(f"{sync_command}\n".encode())
            response = self.serial_port.readline().decode("utf-8").strip()

            await self.query_rtc_time()  # Query time
            await self.query_config()  # Query slots information
            await self.query_status()  # Query autosampler status
            # Safely update the shared status dictionary
            with self.lock:
                self.status.update({"connected": True})
            self.logger.info(f"Connected to {self.serial_port.name}")
            return f"Success: Connected to {self.serial_port.name}"
        except Exception as e:
            await self.disconnect()
            self.logger.error(f"Failed to connect to {self.serial_port.name}: {e}")
            return f"Error: Failed to connect to {self.serial_port.name}: {e}"

    async def disconnect(self) -> str:
        """Disconnect from the serial port asynchronously."""
        try:
            if self.__is_connected():
                self.serial_port.close()  # close the serial port
                self.logger.info(f"Disconnected from {self.serial_port.name}")
                # Reset the status dictionary
                with self.lock:
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
            self.logger.error(f"Failed to disconnect from {self.serial_port.name}: {e}")
            return f"Error: Failed to disconnect from {self.serial_port.name}: {e}"

    async def send_command(self, command: str) -> str:
        """Send command asynchronously."""
        try:
            self.serial_port.write(f"{command.strip()}\n".encode())
            if "time" not in command:
                self.logger.debug(f"PC -> Pico: {command}")
            return f"Success: Command sent: {command}"
        except serial.SerialException as e:
            await self.disconnect()
            self.logger.error(
                f"Error: SerialException from {self.serial_port.name}: {e}"
            )
            return f"Error: SerialException: {e}"
        except serial.SerialTimeoutException as e:
            await self.disconnect()
            self.logger.error(f"Serial Timeout error: {e}")
            return f"Error: Serial Timeout: {e}"
        except Exception as e:
            await self.disconnect()
            self.logger.error(f"Error: Failed to send command: {e}")
            return f"Error: Failed to send command: {e}"

    async def read_serial(self, keyword: str = None) -> str:
        """Read serial data asynchronously, check if specific keyword is in the response."""
        response = None
        try:
            # read the serial port, will block until a response is received
            response = self.serial_port.readline().decode("utf-8").strip()
            if "RTC Time" not in response:  # don't log the RTC time sync response
                self.logger.debug(f"Autosampler -> PC: {response}")
            if "Error" in response:
                self.logger.error(f"{response}")
                response = None
            # check if the keyword is in the response
            if keyword and response and keyword not in response:
                self.logger.warning(
                    f"Expected keyword '{keyword}' not found in response '{response}'"
                )
                response = None
        except serial.SerialException as e:
            await self.disconnect()
            self.logger.error(
                f"Error: SerialException from {self.serial_port.name}: {e}"
            )
        except serial.SerialTimeoutException as e:
            await self.disconnect()
            self.logger.error(f"Timeout error: {e}")
        except Exception as e:
            await self.disconnect()
            self.logger.error(f"Error: {e}")
        finally:
            return response

    async def run_command_and_read(self, command: str, keyword: str, callback):
        """Run send_command and read_serial concurrently using TaskGroup."""
        try:
            if self.__is_connected():
                async with asyncio.TaskGroup() as tg:
                    # Add send_command task to the group
                    send_task = tg.create_task(self.send_command(command))
                    # Add read_serial task to the group
                    read_task = tg.create_task(self.read_serial(keyword))
                response = read_task.result()
                if response:
                    await callback(response)
            else:
                with self.lock:
                    self.status["connected"] = False
                self.logger.error("Not connected to the serial port.")
        except Exception as e:
            self.logger.error(f"Error in run_command_and_read: {e}")

    async def query_rtc_time(self) -> None:
        """Query the RTC time asynchronously."""
        await self.run_command_and_read("time", "RTC Time", self.parse_rtc_time)

    async def parse_rtc_time(self, response: str) -> None:
        """Parse the RTC time from the response."""
        try:
            match = re.search(r"RTC Time: (\d+-\d+-\d+ \d+:\d+:\d+)", response)
            if match:
                rtc_time = match.group(1)
                with self.lock:
                    self.status["rtc_time"] = datetime.strptime(
                        rtc_time, "%Y-%m-%d %H:%M:%S"
                    ).timestamp()
            else:
                self.logger.error(f"Failed to parse RTC time from response: {response}")
        except Exception as e:
            self.logger.error(f"Error parsing RTC time: {e}")

    async def query_config(self) -> None:
        """Query the slots configuration asynchronously."""
        await self.run_command_and_read(
            "config", "Autosampler Configuration", self.parse_config
        )

    async def parse_config(self, response: str) -> None:
        """Parse slots configuration from the Pico."""
        try:
            config_str = response.replace("Autosampler Configuration:", "").strip()
            autosampler_config = json.loads(config_str)
            with self.lock:
                self.status["slots_configuration"] = autosampler_config
                self.status["slots"] = sorted(
                    autosampler_config.keys(),
                    key=lambda x: (
                        not x.isdigit(),
                        int(x) if x.isdigit() else x.lower(),
                    ),
                )
                self.logger.info(f"Slots populated: {self.status['slots']}")
        except json.JSONDecodeError as e:
            self.logger.error(f"Error decoding configuration: {e}")
        except Exception as e:
            self.logger.error(f"Error updating slots configuration: {e}")

    async def query_status(self) -> None:
        """Query the autosampler status asynchronously."""
        await self.run_command_and_read(
            "status", "Autosampler Status", self.parse_status
        )

    async def parse_status(self, response: str) -> None:
        """Parse autosampler status, including position and direction."""
        try:
            match = re.search(r"position: (\d+), direction: (Left|Right)", response)
            if match:
                with self.lock:
                    self.status.update(
                        {
                            "position": int(match.group(1)),
                            "direction": match.group(2),
                        }
                    )
            else:
                self.logger.error(f"Invalid status response: {response}")
        except Exception as e:
            self.logger.error(f"Error parsing status: {e}")

    async def goto_position(self, position: str) -> None:
        """Go to a specific position and update status."""
        if self.__is_connected():
            try:
                if position.isdigit():
                    await self.run_command_and_read(
                        f"position:{position}",
                        "moved to position",  # Look for this keyword in the response
                        self.parse_goto_position,  # Callback to handle the response
                    )
                else:
                    self.logger.error("Invalid position input.")
            except Exception as e:
                self.logger.error(f"Error going to position: {e}")

    # Example response: moved to position 1000 in 0.004037 seconds. relative position: 0
    async def parse_goto_position(self, response: str) -> None:
        """Parse the response from the goto_position command and update status."""
        try:
            match = re.search(
                r"moved to position (\d+) in (\S+) seconds. relative position: (\d+)",
                response,
            )
            if match:
                position = int(match.group(1))
                relative_position = int(match.group(3))
                with self.lock:
                    self.status["position"] = position
                self.logger.info(
                    f"Moved to position {position} (relative position: {relative_position})"
                )
            else:
                self.logger.error(f"Invalid response format for position: {response}")
        except Exception as e:
            self.logger.error(f"Error parsing goto position response: {e}")

    async def goto_slot(self, slot: str) -> None:
        """Go to a specific slot asynchronously and update status."""
        if self.__is_connected():
            try:
                await self.run_command_and_read(
                    f"slot:{slot}",
                    "moved to slot",  # Look for this keyword in the response
                    self.parse_goto_slot,  # Callback to handle the response
                )
            except Exception as e:
                self.logger.error(f"Error going to slot: {e}")

    # Response format: Info: moved to slot 1 in 0.005856 seconds. relative position: 0
    async def parse_goto_slot(self, response: str) -> None:
        """Parse the response from the goto_slot command and update status."""
        try:
            match = re.search(
                r"moved to slot (\S+)",
                response,
            )
            if match:
                slot = str(match.group(1))
                with self.lock:
                    position = self.status["slots_configuration"].get(slot, -1)
                    self.status["position"] = position
                if position == -1:
                    self.logger.error(f"Slot not found in local configuration: {slot}")
                else:
                    self.logger.info(f"Moved to slot {slot} (position: {position})")
            else:
                self.logger.error(f"Invalid response format for slot: {response}")
        except Exception as e:
            self.logger.error(f"Error parsing goto slot response: {e}")

    async def add_slot(self, slot_name: str, slot_position: int) -> None:
        """Add a slot with the specified name and position."""
        try:
            await self.run_command_and_read(
                f"addslot:{slot_name}:{slot_position}",
                f"Success: Slot '{slot_name}' added at position {slot_position}.",
                self.parse_add_slot,
            )
        except Exception as e:
            self.logger.error(f"Error adding slot: {e}")

    async def parse_add_slot(self, response: str) -> None:
        """Parse the response after adding a slot."""
        try:
            if "Success" in response:
                await self.query_config()  # Update configuration after adding a slot
            else:
                self.logger.error(f"Failed to add slot: {response}")
        except Exception as e:
            self.logger.error(f"Error parsing add slot response: {e}")

    async def remove_slot(self, slot_name: str) -> None:
        """Remove a slot with the specified name."""
        try:
            if not slot_name:
                self.logger.error("Invalid slot name.")
                return
            await self.run_command_and_read(
                f"removeslot:{slot_name}",
                f"Success: Slot '{slot_name}' removed.",
                self.parse_remove_slot,
            )
        except Exception as e:
            self.logger.error(f"Error removing slot: {e}")

    async def parse_remove_slot(self, response: str) -> None:
        """Parse the response after removing a slot."""
        try:
            if "Success" in response:
                await self.query_config()  # Update configuration after removing a slot
            else:
                self.logger.error(f"Failed to remove slot: {response}")
        except Exception as e:
            self.logger.error(f"Error parsing remove slot response: {e}")

    async def move_one_step(self, direction: str) -> None:
        """Move the autosampler one step to the left or right."""
        try:
            if direction not in ["left", "right"]:
                self.logger.error("Invalid direction. Use 'left' or 'right'.")
                return
            await self.run_command_and_read(
                f"move:{direction}",
                f"Success: Moved one step",
                self.parse_move_one_step,
            )
        except Exception as e:
            self.logger.error(f"Error moving one step: {e}")

    async def parse_move_one_step(self, response: str) -> None:
        """Parse the response after moving one step."""
        try:
            # format Success: Moved one step Left, current position: 701
            match = re.search(
                r"Moved one step (\S+), current position: (\d+)", response
            )
            direction = match.group(1)
            position = int(match.group(2))
            with self.lock:
                self.status["position"] = position
                self.status["direction"] = direction
            self.logger.info(response)
        except Exception as e:
            self.logger.error(f"Error parsing move one step response: {e}")

    async def save_config(self) -> None:
        """Send a command to save the current configuration to the Pico's storage."""
        try:
            await self.run_command_and_read(
                "save_config",  # Command to save the configuration
                "Success: Configuration saved:",  # Expected success message
                self.parse_save_config,  # Callback to handle the response
            )
        except Exception as e:
            self.logger.error(f"Error saving configuration: {e}")

    async def parse_save_config(self, response: str) -> None:
        """Parse the response after saving the configuration."""
        try:
            if "Success" in response:
                self.logger.info("Configuration saved successfully.")
            else:
                self.logger.error(f"Failed to save configuration: {response}")
        except Exception as e:
            self.logger.error(f"Error parsing save configuration response: {e}")
