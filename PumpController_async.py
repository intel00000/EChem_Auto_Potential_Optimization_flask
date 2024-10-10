import re
import json
import serial
import asyncio
import logging
from datetime import datetime
from multiprocessing import Lock, Manager


class PumpController:
    def __init__(
        self,
        controller_id: int,
        port_name: str,
        serial_timeout: int,
        lock: Lock,
        manager: Manager,
        logger: logging.Logger,
    ):
        self.serial_port = (
            serial.Serial()
        )  # Initialize the serial port but don't open it yet
        self.serial_port.port = port_name
        self.serial_port.baudrate = 115200
        self.serial_port.timeout = None  # Non-blocking read
        self.lock = lock  # Lock to ensure safe access to shared dictionary
        self.logger = logger

        # Shared dictionary to store the status
        self.status = manager.dict(
            {
                "serial_port": self.serial_port.name,
                "controller_id": controller_id,
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "connected": False,
                "pumps_info": {},
                "rtc_time": -1,
            }
        )
        self.logger.info(f"Pump controller {controller_id} created.")

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
            if "Pico Pump Control Version" not in response:
                await self.disconnect()  # Wrong device
                return "Error: Connected to the wrong device."

            # Synchronize the time with PC
            now = datetime.now()
            sync_command = f"0:stime:{now.year}:{now.month}:{now.day}:{now.hour}:{now.minute}:{now.second}"
            self.serial_port.write(f"{sync_command}\n".encode())
            response = self.serial_port.readline().decode("utf-8").strip()

            await self.query_rtc_time()  # Query time
            await self.query_pump_info()  # Query pump information

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
                await self.shutdown()  # Send shutdown signal
                self.serial_port.close()
                self.logger.info(f"Disconnected from {self.serial_port.name}")
                # Reset the status dictionary
                with self.lock:
                    self.status.update(
                        {"connected": False, "pumps_info": {}, "rtc_time": -1}
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
            # Read the serial port, will block until a response is received
            response = self.serial_port.readline().decode("utf-8").strip()
            if "RTC Time" not in response:  # Don't log the RTC time sync response
                self.logger.debug(f"Pico -> PC: {response}")
            if "Error" in response:
                self.logger.error(f"{response}")
                response = None
            # Check if the keyword is in the response
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
                    tg.create_task(self.send_command(command))
                    # Add read_serial task to the group
                    read_task = tg.create_task(self.read_serial(keyword))
                response = read_task.result()
                if response:
                    await callback(response)
            else:
                with self.lock:
                    self.status["connected"] = False
                self.logger.error("Not connected to any device.")
        except Exception as e:
            self.logger.error(f"Error in run_command_and_read: {e}")

    async def query_rtc_time(self) -> None:
        """Query the RTC time asynchronously."""
        await self.run_command_and_read("0:time", "RTC Time", self.parse_rtc_time)

    async def parse_rtc_time(self, response: str) -> None:
        """Parse the RTC time from the response."""
        try:
            # Response format: RTC Time: 2024-9-26 11:47:39
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

    async def query_pump_info(self) -> None:
        """Query pump information asynchronously."""
        await self.run_command_and_read("0:info", "Pump", self.parse_pump_info)

    async def parse_pump_info(self, response: str, clear_existing=True) -> None:
        """Parse the pump info response and update the status."""
        try:
            info_pattern = re.compile(
                r"Pump(\d+) Info: Power Pin: (\d+), Direction Pin: (\d+), Initial Power Pin Value: (\d+), Initial Direction Pin Value: (\d+), Current Power Status: (ON|OFF), Current Direction Status: (CW|CCW)"
            )
            matches = info_pattern.findall(response)
            # Sort the matches by pump_id in ascending order
            matches = sorted(matches, key=lambda x: int(x[0]))
            with self.lock:
                if clear_existing:
                    self.status["pumps_info"].clear()
                for match in matches:
                    (
                        pump_id,
                        power_pin,
                        direction_pin,
                        initial_power_pin_value,
                        initial_direction_pin_value,
                        current_power_status,
                        current_direction_status,
                    ) = match
                    self.status["pumps_info"].update(
                        {
                            int(pump_id): {
                                "power_pin": int(power_pin),
                                "direction_pin": int(direction_pin),
                                "initial_power_pin_value": int(initial_power_pin_value),
                                "initial_direction_pin_value": int(
                                    initial_direction_pin_value
                                ),
                                "current_power_status": current_power_status,
                                "current_direction_status": current_direction_status,
                            }
                        }
                    )
        except Exception as e:
            self.logger.error(f"Error parsing pump info: {e}")

    async def query_status(self) -> None:
        """Query the pump status asynchronously."""
        await self.run_command_and_read("0:st", "Pump", self.parse_pump_status)

    async def parse_pump_status(self, response: str) -> None:
        """Parse pump status and update the status."""
        try:
            status_pattern = re.compile(
                r"Pump(\d+) Status: Power: (ON|OFF), Direction: (CW|CCW)"
            )
            matches = status_pattern.findall(response)
            re_query = False

            with self.lock:
                for match in matches:
                    pump_id, power_status, direction_status = match
                    pump_id = int(pump_id)
                    if pump_id in self.status["pumps_info"]:
                        self.status["pumps_info"][pump_id][
                            "current_power_status"
                        ] = power_status
                        self.status["pumps_info"][pump_id][
                            "current_direction_status"
                        ] = direction_status
                    else:
                        # Received status for a pump that does not exist, requery after releasing the lock
                        self.logger.error(
                            f"Received status update for unknown pump: {pump_id}"
                        )
                        re_query = True
            if re_query:
                await self.query_pump_info()
        except Exception as e:
            self.logger.error(f"Error parsing pump status: {e}")

    async def shutdown(self) -> None:
        """Send shutdown signal to the pumps."""
        try:
            await self.run_command_and_read(
                "0:shutdown", "Success", self.parse_shutdown
            )
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")

    async def parse_shutdown(self, response: str) -> None:
        """Handle response from shutdown command."""
        if "Success" in response:
            self.logger.info("Shutdown successful.")
            await self.query_status()  # update status
        else:
            self.logger.error(f"Shutdown failed: {response}")

    async def reset_pico(self) -> None:
        """Send reset signal to the Pico."""
        if self.is_connected():
            try:
                await self.run_command_and_read("0:reset", "Success", self.parse_reset)
            except Exception as e:
                self.logger.error(f"Error during reset: {e}")

    async def parse_reset(self, response: str) -> None:
        """Handle response from reset command."""
        if "Success" in response:
            self.logger.info("Reset successful.")
            await self.disconnect()
        else:
            self.logger.error(f"Reset failed: {response}")
            
##############################################################################################################
    async def toggle_power(self, pump_id: int) -> None:
        """Toggle power of the specified pump."""
        if self.is_connected():
            try:
                await self.run_command_and_read(
                    f"{pump_id}:pw", "Success", self.parse_toggle_power
                )
            except Exception as e:
                self.logger.error(f"Error toggling power for pump {pump_id}: {e}")

    async def parse_toggle_power(self, response: str) -> None:
        """Handle response from toggle power command."""
        if "Success" in response:
            self.logger.info(response)
            await self.query_status()
        else:
            self.logger.error(f"Failed to toggle power: {response}")

    async def toggle_direction(self, pump_id: int) -> None:
        """Toggle direction of the specified pump."""
        if self.is_connected():
            try:
                await self.run_command_and_read(
                    f"{pump_id}:di", "Success", self.parse_toggle_direction
                )
            except Exception as e:
                self.logger.error(f"Error toggling direction for pump {pump_id}: {e}")

    async def parse_toggle_direction(self, response: str) -> None:
        """Handle response from toggle direction command."""
        if "Success" in response:
            self.logger.info(response)
            await self.query_status()
        else:
            self.logger.error(f"Failed to toggle direction: {response}")

    async def remove_pump(self, pump_id: int) -> None:
        """Remove a pump configuration."""
        if self.is_connected():
            try:
                await self.run_command_and_read(
                    f"{pump_id}:clr", "Success", self.parse_remove_pump
                )
            except Exception as e:
                self.logger.error(f"Error removing pump {pump_id}: {e}")

    async def parse_remove_pump(self, response: str) -> None:
        """Handle response from remove pump command."""
        if "Success" in response:
            self.logger.info(response)
            await self.query_pump_info()
        else:
            self.logger.error(f"Failed to remove pump: {response}")

    async def save_config(self, pump_id: int = 0) -> None:
        """Save pump configuration."""
        if self.is_connected():
            try:
                await self.run_command_and_read(
                    f"{pump_id}:save", "Success", self.parse_save_config
                )
            except Exception as e:
                self.logger.error(f"Error saving configuration for pump {pump_id}: {e}")

    async def parse_save_config(self, response: str) -> None:
        """Handle response from save configuration command."""
        if "Success" in response:
            self.logger.info(response)
        else:
            self.logger.error(f"Failed to save configuration: {response}")

    async def register_pump(
        self,
        pump_id: int,
        power_pin: int,
        direction_pin: int,
        initial_power_pin_value: int,
        initial_direction_pin_value: int,
        initial_power_status: str,
        initial_direction_status: str,
    ) -> None:
        """Register a new pump."""
        if self.is_connected():
            try:
                command = (
                    f"{pump_id}:reg:{power_pin}:{direction_pin}:"
                    f"{initial_power_pin_value}:{initial_direction_pin_value}:"
                    f"{initial_power_status}:{initial_direction_status}"
                )
                await self.run_command_and_read(
                    command, "Success", self.parse_register_pump
                )
            except Exception as e:
                self.logger.error(f"Error registering pump {pump_id}: {e}")

    async def parse_register_pump(self, response: str) -> None:
        """Handle response from register pump command."""
        if "Success" in response:
            self.logger.info(response)
            await self.query_pump_info()
        else:
            self.logger.error(f"Failed to register pump: {response}")
