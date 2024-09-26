# pyserial imports
import serial
import serial.tools.list_ports

# other library
import re
import logging
from queue import Queue
from datetime import datetime

# Custom imports
from Message import simple_Message


class PumpController:
    def __init__(self, controller_id: int, port_name: str, serial_timeout: int):
        self.serial_port = serial.Serial()  # Init the serial port but don't open it yet
        self.serial_port.port = port_name
        self.serial_port.baudrate = 115200
        self.serial_port.timeout = serial_timeout

        # a queue to store commands to be sent to the pump controller
        self.send_command_queue = Queue()

        # Dictionary to store status of this pump controller, this will be read by the backend to update their info
        self.status = {
            "serial_port": self.serial_port.name,
            "controller_id": controller_id,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "connected": False,
            "pumps_info": {},
            "rtc_time": -1,
        }
        logging.info(f"Pump controller {controller_id} created.")

    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    # a function to process all remaining messages in the queue
    def process_all_messages(self) -> None:
        while not self.send_command_queue.empty():
            self.send_command()
        while self.serial_port.in_waiting:
            self.read_serial()

    def connect(self) -> simple_Message:
        """Connect to the serial port."""
        if self.is_connected():
            self.disconnect()
        try:
            self.serial_port.open()
            self.serial_port.reset_input_buffer()  # flush the input and output buffers
            self.serial_port.reset_output_buffer()
            # identify Pico type
            self.serial_port.write("0:ping\n".encode())
            response = self.serial_port.readline().decode("utf-8").strip()
            if "Pico Pump Control Version" not in response:
                self.disconnect()  # we connect to the wrong device
                return simple_Message(
                    "Error",
                    "Connected to the wrong device. Please reconnect to continue.",
                )
            # synchronize the RTC with the PC time
            now = datetime.now()
            sync_command = f"0:stime:{now.year}:{now.month}:{now.day}:{now.hour}:{now.minute}:{now.second}"
            self.serial_port.write(f"{sync_command}\n".encode())
            response = self.serial_port.readline().decode("utf-8").strip()
            self.query_rtc_time()  # Query RTC time
            self.query_pump_info()  # issue a pump info query
            logging.info(f"Connected to {self.serial_port.name}")
            self.status.update({"connected": True})
            self.process_all_messages()
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
                self.shutdown()  # send a shutdown signal
                self.process_all_messages()  # process any remaining messages in the queue
                self.serial_port.close()
                logging.info(f"Disconnected from {self.serial_port.name}")
                self.status.update(
                    {"connected": False, "pumps_info": {}, "rtc_time": -1}
                )
                return simple_Message(
                    "Success", f"Disconnected from {self.serial_port.name}"
                )
        except Exception as e:
            logging.error(f"Failed to disconnect from {self.serial_port.name}: {e}")
            return simple_Message(
                "Error", f"Failed to disconnect from {self.serial_port.name}: {e}"
            )

    # send_command will remove the first item from the queue and send it to force chronological order
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
            logging.error(f"Error: SerialException from {self.serial_port.name}: {e}")
            return simple_Message("Error", f"SerialException: {e}")
        except serial.SerialTimeoutException as e:
            self.disconnect()
            logging.error(f"Timeout error: {e}")
            return simple_Message("Error", f"Serial Timeout: {e}")
        except Exception as e:
            self.disconnect()
            logging.error(f"Error: {e}")
            return simple_Message("Error", f"Error occurred: {e}")

    # set wait to true forces the function to wait for a response
    def read_serial(self, wait=False) -> simple_Message:
        try:
            if self.is_connected() and (self.serial_port.in_waiting or wait):
                response = self.serial_port.readline().decode("utf-8").strip()
                if "RTC Time" not in response:  # don't log the RTC time response
                    logging.debug(f"Pico -> PC: {response}")
                if "Info" in response:
                    self.parse_pump_info(response=response)
                elif "Status" in response:
                    self.parse_pump_status(response)
                elif "RTC Time" in response:
                    self.parse_rtc_time(response)
                elif "Success" in response:
                    return simple_Message("Success", response)
                elif "Error" in response:
                    return simple_Message("Error", response)
                # return a placeholder message if no response will be returned
                return simple_Message("", "")
        except serial.SerialException as e:
            self.disconnect()
            logging.error(f"Error: SerialException from {self.serial_port.name}: {e}")
            return simple_Message("Error", f"SerialException: {e}")
        except serial.SerialTimeoutException as e:
            self.disconnect()
            logging.error(f"Timeout error: {e}")
            return simple_Message("Error", f"Serial Timeout: {e}")
        except Exception as e:
            self.disconnect()
            logging.error(f"Error: {e}")
            return simple_Message("Error", f"Error occurred: {e}")

    def query_rtc_time(self) -> None:
        """Query the RTC time from the Pico."""
        if self.is_connected():
            try:
                self.send_command_queue.put("0:time")
            except Exception as e:
                logging.error(f"Error: {e}")
                return simple_Message("Error", f"An error occurred: {e}")

    def parse_rtc_time(self, response: str) -> None:
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

    def query_pump_info(self) -> None:
        if self.is_connected():
            try:
                self.send_command_queue.put("0:info")
            except Exception as e:
                logging.error(f"Error: {e}")
                return simple_Message("Error", f"An error occurred: {e}")

    def parse_pump_info(self, response: str, clear_existing=True) -> None:
        """Parse the pump info response and update the status."""
        # clear the existing pump info
        if clear_existing:
            self.status["pumps_info"].clear()
        try:
            info_pattern = re.compile(
                r"Pump(\d+) Info: Power Pin: (-?\d+), Direction Pin: (-?\d+), Initial Power Pin Value: (\d+), Initial Direction Pin Value: (\d+), Current Power Status: (ON|OFF), Current Direction Status: (CW|CCW)"
            )
            matches = info_pattern.findall(response)
            # sort the matches by pump_id in ascending order
            matches = sorted(matches, key=lambda x: int(x[0]))
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
                            "power_pin": power_pin,
                            "direction_pin": direction_pin,
                            "initial_power_pin_value": initial_power_pin_value,
                            "initial_direction_pin_value": initial_direction_pin_value,
                            "current_power_status": current_power_status,
                            "current_direction_status": current_direction_status,
                        }
                    }
                )
        except Exception as e:
            logging.error(f"Error: {e}")

    def query_status(self) -> None:
        if self.is_connected():
            try:
                self.send_command_queue.put("0:st")
            except Exception as e:
                logging.error(f"Error: {e}")
                return simple_Message("Error", f"An error occurred: {e}")

    def parse_pump_status(self, response: str) -> None:
        status_pattern = re.compile(
            r"Pump(\d+) Status: Power: (ON|OFF), Direction: (CW|CCW)"
        )
        matches = status_pattern.findall(response)

        for match in matches:
            pump_id, power_status, direction_status = match
            pump_id = int(pump_id)
            pumps_info = self.status["pumps_info"]
            if pump_id in pumps_info:
                pumps_info[pump_id]["current_power_status"] = power_status
                pumps_info[pump_id]["current_direction_status"] = direction_status
            else:
                # This mean we somehow received a status update for a pump that does not exist
                # re-query the pump info
                self.query_pump_info()
                logging.error(
                    f"We received a status update for a pump that does not exist: {pump_id}"
                )

    def shutdown(self) -> None:
        if self.is_connected():
            try:
                self.send_command_queue.put("0:shutdown")
                self.query_status()  # update the status
                logging.info("Signal sent for emergency shutdown.")
            except Exception as e:
                logging.error(f"Error: {e}")

    def reset_pico(self) -> None:
        if self.is_connected():
            try:
                self.send_command_queue.put("0:reset")
                logging.info("Signal sent for Pico reset.")
            except Exception as e:
                logging.error(f"Error: {e}")

    def toggle_power(self, pump_id, update_status=True) -> None:
        if self.is_connected():
            self.send_command_queue.put(f"{pump_id}:pw")
            if update_status:
                self.query_status()

    def toggle_direction(self, pump_id, update_status=True) -> None:
        if self.is_connected():
            self.send_command_queue.put(f"{pump_id}:di")
            if update_status:
                self.query_status()

    def remove_pump(self, pump_id=0) -> None:
        if self.is_connected():
            try:
                self.send_command_queue.put(f"{pump_id}:clr")
                self.query_pump_info()
                logging.info(f"Signal sent to remove pump {pump_id}.")
            except Exception as e:
                logging.error(f"Error: {e}")

    def save_config(self, pump_id=0) -> None:
        if self.is_connected():
            try:
                self.send_command_queue.put(f"{pump_id}:save")
                logging.info(f"Signal sent to save pump {pump_id} configuration.")
                self.query_status()
            except Exception as e:
                logging.error(f"Error: {e}")

    def register_pump(
        self,
        pump_id,
        power_pin,
        direction_pin,
        initial_power_pin_value,
        initial_direction_pin_value,
        initial_power_status,
        initial_direction_status,
    ) -> None:
        if self.is_connected():
            try:
                command = f"{pump_id}:reg:{power_pin}:{direction_pin}:{initial_power_pin_value}:{initial_direction_pin_value}:{initial_power_status}:{initial_direction_status}"
                self.send_command_queue.put(command)
                # issue a pump info query
                self.query_pump_info()
                logging.info(f"Signal sent to register pump {pump_id}.")
            except Exception as e:
                logging.error(f"Error: {e}")
