# pyserial imports
import serial

# weird that I have to import serial again here, wtf
import serial.tools.list_ports

# gui imports
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import tkinterDnD
import pystray
from PIL import Image, ImageDraw

# other library
import os
import re
import sys
import time
import json
import logging

# from decimal import Decimal
from datetime import datetime, timedelta
from decimal import Decimal
from queue import Queue
import pandas as pd

# Define Pi Pico vendor ID
pico_vid = 0x2E8A

global_pad_x = 2
global_pad_y = 2

global_pad_N = 3
global_pad_S = 3
global_pad_W = 3
global_pad_E = 3

NANOSECONDS_PER_DAY = 24 * 60 * 60 * 1_000_000_000
NANOSECONDS_PER_HOUR = 60 * 60 * 1_000_000_000
NANOSECONDS_PER_MINUTE = 60 * 1_000_000_000
NANOSECONDS_PER_SECOND = 1_000_000_000
NANOSECONDS_PER_MILLISECOND = 1_000_000
NANOSECONDS_PER_MICROSECOND = 1_000


class PicoController:
    def __init__(self, master) -> None:
        self.master = master
        self.master.title("Pump Control via Pi Pico")
        self.main_loop_interval_ms = 20  # Main loop interval in milliseconds

        # port refresh timer
        self.port_refresh_interval_ns = (
            5 * NANOSECONDS_PER_SECOND
        )  # Refresh rate for COM ports when not connected
        self.last_port_refresh_ns = -1
        self.timeout = 1  # Serial port timeout in seconds

        # instance fields for the serial port and queue
        self.serial_port = None
        self.current_port = None

        # instance field for the autosampler serial port
        self.serial_port_as = None
        self.current_port_as = None

        # a queue to store commands to be sent to the Pico
        self.send_command_queue = Queue()

        # a queue to store commands to be sent to the autosampler
        self.send_command_queue_as = Queue()

        # Dictionary to store pump information
        self.pumps = {}

        # Dataframe to store the recipe
        self.recipe_df = pd.DataFrame()
        self.recipe_rows = []

        # time stamp for the start of the procedure
        self.start_time_ns = -1
        self.total_procedure_time_ns = -1
        self.current_index = -1
        self.pause_timepoint_ns = -1
        self.pause_duration_ns = 0
        self.scheduled_task = None

        # time stamp for the RTC time query
        self.last_time_query = time.monotonic_ns()

        # define pumps per row in the manual control frame
        self.pumps_per_row = 3

        # define window behavior
        self.image_red = Image.open(resource_path("icons-red.ico"))
        self.image_white = Image.open(resource_path("icons-white.ico"))
        self.first_close = True

        # Set up logging
        runtime = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            os.mkdir("log")
        except FileExistsError:
            pass
        log_filename = os.path.join("log", f"pump_control_run_{runtime}.log")
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s: %(message)s [%(funcName)s]",
            handlers=[logging.FileHandler(log_filename), logging.StreamHandler()],
        )

        self.create_widgets()
        self.master.after(self.main_loop_interval_ms, self.main_loop)

    def create_widgets(self):
        current_row = 0

        # Port selection frame
        self.port_select_frame = ttk.Labelframe(
            self.master,
            text="Select Port",
            padding=(global_pad_N, global_pad_S, global_pad_W, global_pad_E),
        )
        self.port_select_frame.grid(
            row=current_row,
            column=0,
            columnspan=5,
            rowspan=3,
            padx=global_pad_x,
            pady=global_pad_y,
            sticky="NSEW",
        )
        # first row in the port_select_frame
        self.port_label = ttk.Label(
            self.port_select_frame, text="Pump Controller Port:"
        )
        self.port_label.grid(
            row=0, column=0, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.port_combobox = ttk.Combobox(
            self.port_select_frame, state="readonly", width=30
        )
        self.port_combobox.grid(row=0, column=1, padx=global_pad_x, pady=global_pad_y)
        self.connect_button = ttk.Button(
            self.port_select_frame, text="Connect", command=self.connect_to_pico
        )
        self.connect_button.grid(row=0, column=2, padx=global_pad_x, pady=global_pad_y)
        self.disconnect_button = ttk.Button(
            self.port_select_frame, text="Disconnect", command=self.disconnect_pico
        )
        self.disconnect_button.grid(
            row=0, column=3, padx=global_pad_x, pady=global_pad_y
        )
        self.disconnect_button.config(state=tk.DISABLED)
        self.reset_button = ttk.Button(
            self.port_select_frame, text="Hard reset", command=self.reset_pico
        )
        self.reset_button.grid(
            row=0, column=4, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.reset_button.config(state=tk.DISABLED)
        # second row in the port_select_frame
        self.port_label_as = ttk.Label(self.port_select_frame, text="Autosampler Port:")
        self.port_label_as.grid(
            row=1, column=0, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.port_combobox_as = ttk.Combobox(
            self.port_select_frame, state="readonly", width=30
        )
        self.port_combobox_as.grid(
            row=1, column=1, padx=global_pad_x, pady=global_pad_y
        )
        self.connect_button_as = ttk.Button(
            self.port_select_frame, text="Connect", command=self.connect_to_pico_as
        )
        self.connect_button_as.grid(
            row=1, column=2, padx=global_pad_x, pady=global_pad_y
        )
        self.disconnect_button_as = ttk.Button(
            self.port_select_frame, text="Disconnect", command=self.disconnect_pico_as
        )
        self.disconnect_button_as.grid(
            row=1, column=3, padx=global_pad_x, pady=global_pad_y
        )
        self.disconnect_button_as.config(state=tk.DISABLED)
        # third row in the port_select_frame
        self.status_label = ttk.Label(
            self.port_select_frame, text="Pump Controller Status: Not connected"
        )
        self.status_label.grid(
            row=2,
            column=0,
            padx=global_pad_x,
            pady=global_pad_y,
            columnspan=2,
            sticky="W",
        )
        self.status_label_as = ttk.Label(
            self.port_select_frame, text="Autosampler Controller Status: Not connected"
        )
        self.status_label_as.grid(
            row=2,
            column=2,
            padx=global_pad_x,
            pady=global_pad_y,
            columnspan=3,
            sticky="W",
        )
        # update the current row
        current_row += self.port_select_frame.grid_size()[1]

        # Pump Manual Control frame
        self.manual_control_frame = ttk.Labelframe(
            self.master,
            text="Pump Manual Control",
            padding=(global_pad_N, global_pad_S, global_pad_W, global_pad_E),
        )
        self.manual_control_frame.grid(
            row=current_row,
            column=0,
            columnspan=5,
            padx=global_pad_x,
            pady=global_pad_y,
            sticky="NSEW",
        )
        # first row in the manual control frame, containing all the buttons
        self.manual_control_frame_buttons = ttk.Frame(self.manual_control_frame)
        self.manual_control_frame_buttons.grid(
            row=0,
            column=0,
            columnspan=5,
            padx=global_pad_x,
            pady=global_pad_y,
            sticky="NSEW",
        )
        self.add_pump_button = ttk.Button(
            self.manual_control_frame_buttons, text="Add Pump", command=self.add_pump
        )
        self.add_pump_button.grid(
            row=0, column=0, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.add_pump_button.config(state=tk.DISABLED)
        self.clear_pumps_button = ttk.Button(
            self.manual_control_frame_buttons,
            text="Clear All Pumps",
            command=self.clear_pumps,
        )
        self.clear_pumps_button.grid(
            row=0, column=1, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.clear_pumps_button.config(state=tk.DISABLED)
        self.save_pumps_button = ttk.Button(
            self.manual_control_frame_buttons,
            text="Save Config",
            command=lambda: self.save_pump_config(0),
        )
        self.save_pumps_button.grid(
            row=0, column=2, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.save_pumps_button.config(state=tk.DISABLED)
        self.emergency_shutdown_button = ttk.Button(
            self.manual_control_frame_buttons,
            text="Emergency Shutdown",
            command=lambda: self.emergency_shutdown(True),
        )
        self.emergency_shutdown_button.grid(
            row=0, column=3, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.emergency_shutdown_button.config(state=tk.DISABLED)
        # second row in the manual control frame, containing the pumps widgets
        self.pumps_frame = ttk.Frame(self.manual_control_frame)
        self.pumps_frame.grid(
            row=1,
            column=0,
            columnspan=5,
            padx=global_pad_x,
            pady=global_pad_y,
            sticky="NSEW",
        )
        # update the current row
        current_row += self.manual_control_frame.grid_size()[1]

        # Autosampler Manual Control frame
        self.manual_control_frame_as = ttk.Labelframe(
            self.master,
            text="Autosampler Manual Control",
            padding=(global_pad_N, global_pad_S, global_pad_W, global_pad_E),
        )
        self.manual_control_frame_as.grid(
            row=current_row,
            column=0,
            columnspan=5,
            padx=global_pad_x,
            pady=global_pad_y,
            sticky="NSEW",
        )
        # Text Entry for Position
        self.position_entry_as = ttk.Entry(self.manual_control_frame_as, width=15)
        self.position_entry_as.grid(
            row=0, column=1, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.goto_position_button_as = ttk.Button(
            self.manual_control_frame_as,
            text="Go to Position",
            command=self.goto_position_as,
        )
        self.goto_position_button_as.grid(
            row=0, column=2, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.goto_position_button_as.config(state=tk.DISABLED)
        # Dropdown and Button for Slots
        self.slot_combobox_as = ttk.Combobox(
            self.manual_control_frame_as, state="readonly", width=15
        )
        self.slot_combobox_as.grid(
            row=0, column=3, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.goto_slot_button_as = ttk.Button(
            self.manual_control_frame_as, text="Go to Slot", command=self.goto_slot_as
        )
        self.goto_slot_button_as.grid(
            row=0, column=4, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.goto_slot_button_as.config(state=tk.DISABLED)
        # update the current row
        current_row += self.manual_control_frame_as.grid_size()[1]

        # Recipe frame
        self.recipe_frame = ttk.Labelframe(
            self.master,
            text="Recipe",
            padding=(global_pad_N, global_pad_S, global_pad_W, global_pad_E),
        )
        self.recipe_frame.grid(
            row=current_row,
            column=0,
            columnspan=5,
            padx=global_pad_x,
            pady=global_pad_y,
            sticky="NSEW",
        )
        # first row in the recipe frame, containing the buttons
        self.recipe_frame_buttons = ttk.Frame(self.recipe_frame)
        self.recipe_frame_buttons.grid(
            row=0,
            column=0,
            columnspan=5,
            padx=global_pad_x,
            pady=global_pad_y,
            sticky="NSEW",
        )
        self.load_recipe_button = ttk.Button(
            self.recipe_frame_buttons, text="Load Recipe", command=self.load_recipe
        )
        self.load_recipe_button.grid(
            row=0, column=0, padx=global_pad_x, pady=global_pad_y
        )
        self.start_button = ttk.Button(
            self.recipe_frame_buttons, text="Start", command=self.start_procedure
        )
        self.start_button.grid(row=0, column=1, padx=global_pad_x, pady=global_pad_y)
        self.start_button.config(state=tk.DISABLED)
        self.stop_button = ttk.Button(
            self.recipe_frame_buttons,
            text="Stop",
            command=lambda: self.stop_procedure(True),
        )
        self.stop_button.grid(row=0, column=2, padx=global_pad_x, pady=global_pad_y)
        self.stop_button.config(state=tk.DISABLED)
        self.pause_button = ttk.Button(
            self.recipe_frame_buttons, text="Pause", command=self.pause_procedure
        )
        self.pause_button.grid(row=0, column=3, padx=global_pad_x, pady=global_pad_y)
        self.pause_button.config(state=tk.DISABLED)
        self.continue_button = ttk.Button(
            self.recipe_frame_buttons, text="Continue", command=self.continue_procedure
        )
        self.continue_button.grid(row=0, column=4, padx=global_pad_x, pady=global_pad_y)
        self.continue_button.config(state=tk.DISABLED)
        # second row in the recipe frame, containing the recipe table
        self.recipe_table_frame = ttk.Frame(self.recipe_frame)
        self.recipe_table_frame.grid(
            row=1,
            column=0,
            columnspan=5,
            padx=global_pad_x,
            pady=global_pad_y,
            sticky="NSEW",
        )
        self.recipe_table = ttk.Frame(self.recipe_table_frame)
        self.recipe_table.grid(
            row=0, column=0, padx=global_pad_x, pady=global_pad_y, sticky="NSEW"
        )
        self.scrollbar = ttk.Scrollbar()
        # update the current row
        current_row += self.recipe_frame.grid_size()[1]

        # Progress frame
        self.progress_frame = ttk.Labelframe(
            self.master,
            text="Progress",
            padding=(global_pad_N, global_pad_S, global_pad_W, global_pad_E),
        )
        self.progress_frame.grid(
            row=current_row,
            column=0,
            columnspan=5,
            padx=global_pad_x,
            pady=global_pad_y,
            sticky="NSEW",
        )
        # first row in the progress frame, containing the progress bar
        self.total_progress_label = ttk.Label(
            self.progress_frame, text="Total Progress:"
        )
        self.total_progress_label.grid(
            row=0, column=0, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.total_progress_bar = ttk.Progressbar(
            self.progress_frame, length=250, mode="determinate"
        )
        self.total_progress_bar.grid(
            row=0, column=1, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        # second row in the progress frame, containing the remaining time and Procedure end time
        self.remaining_time_label = ttk.Label(
            self.progress_frame, text="Remaining Time:"
        )
        self.remaining_time_label.grid(
            row=1, column=0, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.remaining_time_value = ttk.Label(self.progress_frame, text="")
        self.remaining_time_value.grid(
            row=1, column=1, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.end_time_label = ttk.Label(self.progress_frame, text="End Time:")
        self.end_time_label.grid(
            row=1, column=2, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        self.end_time_value = ttk.Label(self.progress_frame, text="")
        self.end_time_value.grid(
            row=1, column=3, padx=global_pad_x, pady=global_pad_y, sticky="W"
        )
        # update the current row
        current_row += self.progress_frame.grid_size()[1]

        # RTC time frame
        self.rtc_time_frame = ttk.Frame(
            self.master,
            padding=(0, 0, 0, 0),
        )
        self.rtc_time_frame.grid(
            row=current_row,
            column=0,
            columnspan=5,
            padx=0,
            pady=0,
            sticky="NSE",
        )
        # first row in the rtc_time_frame, containing the current rtc time from the Pico
        self.current_time_label = ttk.Label(
            self.rtc_time_frame, text="Pump Controller Time: --:--:--"
        )
        self.current_time_label.grid(row=0, column=0, padx=0, pady=0, sticky="NSE")
        self.current_time_label_as = ttk.Label(
            self.rtc_time_frame, text="Autosampler Controller Time: --:--:--"
        )
        self.current_time_label_as.grid(row=0, column=1, padx=0, pady=0, sticky="NSE")

    def main_loop(self):
        try:
            self.refresh_ports()
            self.read_serial()
            self.send_command()
            self.read_serial_as()
            self.send_command_as()
            self.update_progress()
            self.query_rtc_time()
            self.master.after(self.main_loop_interval_ms, self.main_loop)
        except Exception as e:
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox("Error", f"An error occurred: {e}")
            # we will continue the main loop even if an error occurs
            self.master.after(self.main_loop_interval_ms, self.main_loop)

    def refresh_ports(self, instant=False):
        if not self.serial_port or not self.serial_port_as:
            if (
                time.monotonic_ns() - self.last_port_refresh_ns
                < self.port_refresh_interval_ns
                and not instant
            ):
                return
            # filter by vendor id and ignore already connected ports
            if self.serial_port:
                pump_controller_port = self.serial_port.name.strip()
            else:
                pump_controller_port = None
            if self.serial_port_as:
                autosampler_port = self.serial_port_as.name.strip()
            else:
                autosampler_port = None

            ports = [
                port.device + " (SN:" + str(port.serial_number) + ")"
                for port in serial.tools.list_ports.comports()
                if port.vid == pico_vid
                and port.name.strip() != pump_controller_port
                and port.name.strip() != autosampler_port
            ]
            ports_list = [
                port
                for port in serial.tools.list_ports.comports()
                if port.vid == pico_vid
                and port.name.strip() != pump_controller_port
                and port.name.strip() != autosampler_port
            ]
            # print detail information of the ports to the console
            for port in ports_list:
                try:
                    # put these into one line
                    logging.debug(
                        f"name: {port.name}, description: {port.description}, device: {port.device}, hwid: {port.hwid}, manufacturer: {port.manufacturer}, pid: {hex(port.pid)}, serial_number: {port.serial_number}, vid: {hex(port.vid)}"
                    )
                except Exception as e:
                    logging.error(f"Error: {e}")

            if not self.serial_port:
                self.port_combobox["values"] = ports
                if len(ports) > 0:
                    self.port_combobox.current(0)
                else:
                    self.port_combobox.set("")  # clear the port combobox
            if not self.serial_port_as:
                self.port_combobox_as["values"] = ports
                if len(ports) > 0:
                    self.port_combobox_as.current(0)
                else:
                    self.port_combobox_as.set("")
            self.last_port_refresh_ns = time.monotonic_ns()

    def connect_to_pico(self):
        selected_port = self.port_combobox.get()
        if selected_port:
            parsed_port = selected_port.split("(")[0].strip()
            if self.serial_port:  # Check if already connected
                if (  # if already connected, pop a confirmation message before disconnecting
                    messagebox.askyesno(
                        "Disconnect",
                        f"Disconnect from current port {parsed_port}?",
                    )
                    == tk.YES
                ):
                    # suppress the message for the disconnect
                    self.disconnect_pico(show_message=False)
                else:
                    return

            try:  # Attempt to connect to the selected port
                self.serial_port = serial.Serial(parsed_port, timeout=self.timeout)
                self.current_port = selected_port

                self.status_label.config(
                    text=f"Pump Controller Status: Connected to {parsed_port}"
                )
                logging.info(f"Connected to {selected_port}")
                self.send_command_queue.put("0:ping")  # ping to identify the Pico
                self.refresh_ports(instant=True)  # refresh the ports immediately

                self.sync_rtc_with_pc_time(queue=self.send_command_queue)
                self.query_pump_info()  # issue a pump info query
                self.enable_disable_pumps_buttons(tk.NORMAL)  # enable the buttons
            except serial.SerialException as e:
                self.status_label.config(text="Pump Controller Status: Not connected")
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox(
                    "Connection Status",
                    f"Failed to connect to {selected_port} with error: {e}",
                )
            except Exception as e:
                self.status_label.config(text="Pump Controller Status: Not connected")
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def connect_to_pico_as(self):
        selected_port = self.port_combobox_as.get()
        if selected_port:
            parsed_port = selected_port.split("(")[0].strip()
            if self.serial_port_as:
                if (
                    messagebox.askyesno(
                        "Disconnect",
                        f"Disconnect from current port {parsed_port}?",
                    )
                    == tk.YES
                ):
                    self.disconnect_pico_as(show_message=False)
                else:
                    return
            try:
                self.serial_port_as = serial.Serial(parsed_port, timeout=self.timeout)
                self.current_port_as = selected_port
                self.status_label_as.config(
                    text=f"Autosampler Controller Status: Connected to {parsed_port}"
                )
                logging.info(f"Connected to Autosampler at {selected_port}")
                self.send_command_queue_as.put("0:ping")  # Ping to identify the Pico
                self.refresh_ports(instant=True)
                self.enable_disable_autosampler_buttons(tk.NORMAL)
                self.send_command_queue_as.put("config")  # Populate the slots
                self.sync_rtc_with_pc_time(queue=self.send_command_queue_as)
            except serial.SerialException as e:
                self.status_label_as.config(
                    text="Autosampler Controller Status: Not connected"
                )
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox(
                    "Connection Status",
                    f"Failed to connect to {selected_port} with error: {e}",
                )
            except Exception as e:
                self.status_label_as.config(
                    text="Autosampler Controller Status: Not connected"
                )
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def enable_disable_autosampler_buttons(self, state) -> None:
        self.disconnect_button_as.config(state=state)
        self.position_entry_as.config(state=state)
        self.goto_position_button_as.config(state=state)
        self.slot_combobox_as.config(state=state)
        self.goto_slot_button_as.config(state=state)

    def sync_rtc_with_pc_time(self, queue: Queue) -> None:
        """Synchronize the Pico's RTC with the PC's time."""
        try:
            now = datetime.now()
            sync_command = f"0:stime:{now.year}:{now.month}:{now.day}:{now.hour}:{now.minute}:{now.second}"
            queue.put(sync_command)
        except Exception as e:
            logging.error(f"Error synchronizing RTC with PC time: {e}")

    def query_rtc_time(self) -> None:
        """Send a request to the Pico to get the current RTC time every second."""
        current_time = time.monotonic_ns()
        if current_time - self.last_time_query >= NANOSECONDS_PER_SECOND:
            if self.serial_port:
                self.send_command_queue.put("0:time")
            if self.serial_port_as:
                self.send_command_queue_as.put("time")
            self.last_time_query = current_time

    def update_rtc_time_display(self, response, is_Autosampler=False) -> None:
        try:
            match = re.search(r"RTC Time: (\d+-\d+-\d+ \d+:\d+:\d+)", response)
            if match and not is_Autosampler:
                rtc_time = match.group(1)
                self.current_time_label.config(text=f"Pump Controller Time: {rtc_time}")
            if match and is_Autosampler:
                rtc_time = match.group(1)
                self.current_time_label_as.config(
                    text=f"Autosampler Controller Time: {rtc_time}"
                )
        except Exception as e:
            logging.error(f"Error updating RTC time display: {e}")

    # a helper function to enable/disable the buttons
    def enable_disable_pumps_buttons(self, state):
        self.disconnect_button.config(state=state)
        self.reset_button.config(state=state)
        self.add_pump_button.config(state=state)
        self.clear_pumps_button.config(state=state)
        self.save_pumps_button.config(state=state)
        self.emergency_shutdown_button.config(state=state)

    def disconnect_pico(self, show_message=True):
        if self.serial_port:
            try:
                self.serial_port.close()  # close the serial port connection
                self.serial_port = None
                self.current_port = None

                # cancel the scheduled task if it exists
                if self.scheduled_task:
                    self.master.after_cancel(self.scheduled_task)
                    self.scheduled_task = None

                # update UI
                self.status_label.config(text="Pump Controller Status: Not connected")

                self.clear_pumps_widgets()  # clear the pumps widgets
                self.clear_recipe()  # clear the recipe table

                self.enable_disable_pumps_buttons(tk.DISABLED)  # disable buttons

                while not self.send_command_queue.empty():
                    self.send_command_queue.get()  # empty the queue

                self.refresh_ports(instant=True)  # refresh the ports immediately

                logging.info("Disconnected from Pico")
                if show_message:
                    self.non_blocking_messagebox(
                        "Connection Status", "Disconnected from Pico"
                    )
            except serial.SerialException as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox(
                    "Connection Status", "Failed to disconnect from Pico"
                )
            except Exception as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def disconnect_pico_as(self, show_message=True):
        if self.serial_port_as:
            try:
                self.serial_port_as.close()
                self.serial_port_as = None
                self.current_port_as = None

                self.status_label_as.config(
                    text="Autosampler Controller Status: Not connected"
                )
                self.slot_combobox_as.set("")
                self.enable_disable_autosampler_buttons(tk.DISABLED)

                while not self.send_command_queue_as.empty():  # empty the queue
                    self.send_command_queue_as.get()

                self.refresh_ports(instant=True)

                logging.info("Disconnected from Autosampler")
                if show_message:
                    self.non_blocking_messagebox(
                        "Connection Status", "Disconnected from Autosampler"
                    )
            except serial.SerialException as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox(
                    "Connection Status", "Failed to disconnect from Autosampler"
                )
            except Exception as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def query_pump_info(self):
        if self.serial_port:
            # put the command in the queue
            self.send_command_queue.put("0:info")

    def update_status(self):
        if self.serial_port:
            # put the command in the queue
            self.send_command_queue.put("0:st")

    def toggle_power(self, pump_id, update_status=True):
        if self.serial_port:
            self.send_command_queue.put(f"{pump_id}:pw")
            if update_status:
                self.update_status()

    def toggle_direction(self, pump_id, update_status=True):
        if self.serial_port:
            # put the command in the queue
            self.send_command_queue.put(f"{pump_id}:di")
            if update_status:
                self.update_status()

    def register_pump(
        self,
        pump_id,
        power_pin,
        direction_pin,
        initial_power_pin_value,
        initial_direction_pin_value,
        initial_power_status,
        initial_direction_status,
    ):
        if self.serial_port:
            try:
                command = f"{pump_id}:reg:{power_pin}:{direction_pin}:{initial_power_pin_value}:{initial_direction_pin_value}:{initial_power_status}:{initial_direction_status}"
                self.send_command_queue.put(command)
                self.update_status()
            except Exception as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def clear_pumps(self, pump_id=0):
        if self.serial_port:
            try:
                # pop a message to confirm the clear
                if pump_id == 0:
                    if messagebox.askyesno("Clear Pumps", "Clear all pumps?") == tk.YES:
                        self.send_command_queue.put("0:clr")
                        self.clear_pumps_widgets()
                        # issue a pump info query
                        self.query_pump_info()
                else:
                    if (
                        messagebox.askyesno("Clear Pump", f"Clear pump {pump_id}?")
                        == tk.YES
                    ):
                        self.send_command_queue.put(f"{pump_id}:clr")
                        self.clear_pumps_widgets()
                        # issue a pump info query
                        self.query_pump_info()
            except Exception as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def save_pump_config(self, pump_id=0):
        if self.serial_port:
            try:
                self.send_command_queue.put(f"{pump_id}:save")
                logging.info(f"Signal sent to save pump {pump_id} configuration.")
            except Exception as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def emergency_shutdown(self, confirmation=False):
        if self.serial_port:
            try:
                if not confirmation or messagebox.askyesno(
                    "Emergency Shutdown",
                    "Are you sure you want to perform an emergency shutdown?",
                ):
                    self.send_command_queue.put("0:shutdown")
                    # update the status
                    self.update_status()
                    logging.info("Signal sent for emergency shutdown.")
            except Exception as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def reset_pico(self):
        if self.serial_port:
            try:
                if messagebox.askyesno(
                    "Reset", "Are you sure you want to reset the Pico?"
                ):
                    self.send_command_queue.put("0:reset")
                    logging.info("Signal sent for Pico reset.")
                    self.enable_disable_pumps_buttons(tk.DISABLED)
            except Exception as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def stop_procedure(self, message=False):
        try:
            if self.scheduled_task:
                self.master.after_cancel(self.scheduled_task)
                self.scheduled_task = None
            self.start_time_ns = -1
            self.total_procedure_time_ns = -1
            self.current_index = -1
            self.pause_timepoint_ns = -1
            self.pause_duration_ns = 0
            # call a emergency shutdown in case the power is still on
            self.emergency_shutdown()
            # update the status
            self.update_status()
            # disable the buttons
            self.stop_button.config(state=tk.DISABLED)
            self.pause_button.config(state=tk.DISABLED)
            self.continue_button.config(state=tk.DISABLED)
            # enable the disconnect button
            self.disconnect_button.config(state=tk.NORMAL)
            logging.info("Procedure stopped.")
            if message:
                self.non_blocking_messagebox(
                    "Procedure Stopped", "The procedure has been stopped."
                )
        except Exception as e:
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def pause_procedure(self):
        try:
            if self.scheduled_task:
                self.master.after_cancel(self.scheduled_task)
                self.scheduled_task = None
            self.pause_timepoint_ns = time.monotonic_ns()
            self.pause_button.config(state=tk.DISABLED)
            self.continue_button.config(state=tk.NORMAL)
            self.end_time_value.config(text="")
            logging.info("Procedure paused.")
        except Exception as e:
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def continue_procedure(self):
        try:
            if self.pause_timepoint_ns != -1:
                self.pause_duration_ns += time.monotonic_ns() - self.pause_timepoint_ns
                self.pause_timepoint_ns = -1
            self.pause_button.config(state=tk.NORMAL)
            self.continue_button.config(state=tk.DISABLED)
            self.execute_procedure(self.current_index)
            logging.info("Procedure continued.")
        except Exception as e:
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    # send_command will remove the first item from the queue and send it
    def send_command(self):
        try:
            if self.serial_port and not self.send_command_queue.empty():
                command = self.send_command_queue.get(block=False)
                self.serial_port.write(f"{command}\n".encode())
                # don't log the RTC time sync command
                if "time" not in command:
                    logging.debug(f"PC -> Pico: {command}")
        except serial.SerialException as e:
            self.disconnect_pico(False)
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox(
                "Connection Error",
                "Connection to Pico lost. Please reconnect to continue.",
            )
        except Exception as e:
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox(
                "Error", f"Send_command: An error occurred: {e}"
            )

    def send_command_as(self):
        try:
            if self.serial_port_as and not self.send_command_queue_as.empty():
                command = self.send_command_queue_as.get(block=False)
                self.serial_port_as.write(f"{command}\n".encode())
                if "time" not in command:
                    logging.debug(f"PC -> Autosampler: {command}")
        except serial.SerialException as e:
            self.disconnect_pico_as(False)
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox(
                "Connection Error",
                "Connection to Autosampler lost. Please reconnect to continue.",
            )
        except Exception as e:
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox(
                "Error", f"Send_command_as: An error occurred: {e}"
            )

    def read_serial(self):
        try:
            if self.serial_port and self.serial_port.in_waiting:
                response = self.serial_port.readline().decode("utf-8").strip()
                # don't log the RTC time response
                if "RTC Time" not in response:
                    logging.debug(f"Pico -> PC: {response}")

                if "Info" in response:
                    self.add_pump_widgets(response)
                elif "Ping" in response:
                    if "Pump" not in response:
                        # we connect to the wrong device
                        self.non_blocking_messagebox(
                            "Connection Error",
                            "Connected to the wrong device. Please reconnect to continue.",
                        )
                        self.disconnect_pico(False)
                elif "Status" in response:
                    self.update_pump_status(response)
                elif "RTC Time" in response:
                    self.update_rtc_time_display(response)
                elif "Success" in response:
                    self.non_blocking_messagebox("Success", response)
                elif "Error" in response:
                    self.non_blocking_messagebox("Error", response)
        except serial.SerialException as e:
            self.disconnect_pico(False)
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox(
                "Connection Error",
                "Connection to Pico lost. Please reconnect to continue.",
            )
        except Exception as e:
            self.disconnect_pico()
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox(
                "Error", f"Read_serial: An error occurred: {e}"
            )

    def read_serial_as(self):
        try:
            if self.serial_port_as and self.serial_port_as.in_waiting:
                response = self.serial_port_as.readline().decode("utf-8").strip()

                if "RTC Time" not in response:
                    logging.debug(f"Autosampler -> PC: {response}")

                if "Autosampler Configuration:" in response:
                    # Extract the JSON part of the response
                    config_str = response.replace(
                        "Autosampler Configuration:", ""
                    ).strip()
                    try:
                        autosampler_config = json.loads(config_str)
                        slots = list(autosampler_config.keys())
                        slots.sort()
                        self.slot_combobox_as["values"] = slots
                        if slots:
                            self.slot_combobox_as.current(
                                0
                            )  # Set the first slot as default
                        logging.info(f"Slots populated: {slots}")
                    except json.JSONDecodeError as e:
                        logging.error(f"Error decoding autosampler configuration: {e}")
                        self.non_blocking_messagebox(
                            "Error", "Failed to decode autosampler configuration."
                        )
                elif "Ping" in response:
                    if "Autosampler" not in response:
                        self.non_blocking_messagebox(
                            "Connection Error",
                            "Connected to the wrong device. Please reconnect to continue.",
                        )
                        self.disconnect_pico_as()
                elif "RTC Time" in response:
                    self.update_rtc_time_display(response, is_Autosampler=True)
                elif "Error" in response:
                    self.non_blocking_messagebox("Error", response)
                elif "Success" in response:
                    self.non_blocking_messagebox("Success", response)

        except serial.SerialException as e:
            self.disconnect_pico_as(False)
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox(
                "Connection Error",
                "Connection to Autosampler lost. Please reconnect to continue.",
            )
        except Exception as e:
            self.disconnect_pico_as()
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox(
                "Error", f"Read_serial_as: An error occurred: {e}"
            )

    def goto_position_as(self, position=None):
        if self.serial_port_as:
            try:
                if position is None:
                    position = self.position_entry_as.get().strip()
                if position and position.isdigit():
                    command = f"position:{position}"
                    self.send_command_queue_as.put(command)
                    logging.info(f"Autosampler command sent: {command}")
                else:
                    self.non_blocking_messagebox(
                        "Error", "Invalid input, please enter a valid position number."
                    )
            except Exception as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def goto_slot_as(self, slot=None):
        if self.serial_port_as:
            try:
                if slot is None:
                    slot = self.slot_combobox_as.get().strip()
                if slot:
                    command = f"slot:{slot}"
                    self.send_command_queue_as.put(command)
            except Exception as e:
                logging.error(f"Error: {e}")
                self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def add_pump_widgets(self, response):
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
                    power_status,
                    direction_status,
                ) = match
                pump_id = int(pump_id)
                if pump_id in self.pumps:
                    self.pumps[pump_id].update(
                        {
                            "power_pin": power_pin,
                            "direction_pin": direction_pin,
                            "initial_power_pin_value": initial_power_pin_value,
                            "initial_direction_pin_value": initial_direction_pin_value,
                            "power_status": power_status,
                            "direction_status": direction_status,
                        }
                    )

                    pump_frame = self.pumps[pump_id]["frame"]
                    pump_frame.grid(
                        row=(pump_id - 1) // self.pumps_per_row,
                        column=(pump_id - 1) % self.pumps_per_row,
                        padx=global_pad_x,
                        pady=global_pad_y,
                        sticky="NSWE",
                    )

                    self.pumps[pump_id]["power_label"].config(
                        text=f"Power Status: {power_status}"
                    )
                    self.pumps[pump_id]["direction_label"].config(
                        text=f"Direction Status: {direction_status}"
                    )
                    self.pumps[pump_id]["power_button"].config(
                        state="normal" if power_pin != "-1" else "disabled"
                    )
                    self.pumps[pump_id]["direction_button"].config(
                        state="normal" if direction_pin != "-1" else "disabled"
                    )
                    self.pumps[pump_id]["frame"].config(
                        text=f"Pump {pump_id}, Power pin: {power_pin}, Direction pin: {direction_pin}"
                    )
                else:
                    # pump does not exist, create a new pump frame
                    pump_frame = ttk.Labelframe(
                        self.pumps_frame,
                        text=f"Pump {pump_id}, Power pin: {power_pin}, Direction pin: {direction_pin}",
                        labelanchor="n",
                    )
                    pump_frame.grid(
                        row=(pump_id - 1) // self.pumps_per_row,
                        column=(pump_id - 1) % self.pumps_per_row,
                        padx=global_pad_x,
                        pady=global_pad_y,
                        sticky="NSWE",
                    )

                    # first row in the pump frame
                    power_label = ttk.Label(
                        pump_frame, text=f"Power Status: {power_status}"
                    )
                    power_label.grid(
                        row=0,
                        column=0,
                        padx=global_pad_x,
                        pady=global_pad_y,
                        sticky="NS",
                    )
                    direction_label = ttk.Label(
                        pump_frame, text=f"Direction Status: {direction_status}"
                    )
                    direction_label.grid(
                        row=0,
                        column=1,
                        padx=global_pad_x,
                        pady=global_pad_y,
                        sticky="NS",
                    )

                    # second row in the pump frame
                    power_button = ttk.Button(
                        pump_frame,
                        text="Toggle Power",
                        command=lambda pid=pump_id: self.toggle_power(pid),
                        state="disabled" if power_pin == "-1" else "normal",
                    )
                    power_button.grid(
                        row=1,
                        column=0,
                        padx=global_pad_x,
                        pady=global_pad_y,
                        sticky="NS",
                    )
                    direction_button = ttk.Button(
                        pump_frame,
                        text="Toggle Direction",
                        command=lambda pid=pump_id: self.toggle_direction(pid),
                        state="disabled" if direction_pin == "-1" else "normal",
                    )
                    direction_button.grid(
                        row=1,
                        column=1,
                        padx=global_pad_x,
                        pady=global_pad_y,
                        sticky="NS",
                    )

                    # third row in the pump frame
                    remove_button = ttk.Button(
                        pump_frame,
                        text="Remove",
                        command=lambda pid=pump_id: self.clear_pumps(pid),
                    )
                    remove_button.grid(
                        row=2,
                        column=0,
                        padx=global_pad_x,
                        pady=global_pad_y,
                        sticky="NS",
                    )
                    edit_button = ttk.Button(
                        pump_frame,
                        text="Edit",
                        command=lambda pid=pump_id: self.edit_pump(pid),
                    )
                    edit_button.grid(
                        row=2,
                        column=1,
                        padx=global_pad_x,
                        pady=global_pad_y,
                        sticky="NS",
                    )

                    self.pumps[pump_id] = {
                        "power_pin": power_pin,
                        "direction_pin": direction_pin,
                        "initial_power_pin_value": initial_power_pin_value,
                        "initial_direction_pin_value": initial_direction_pin_value,
                        "power_status": power_status,
                        "direction_status": direction_status,
                        "frame": pump_frame,
                        "power_label": power_label,
                        "direction_label": direction_label,
                        "power_button": power_button,
                        "direction_button": direction_button,
                    }
        except Exception as e:
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    # a function to clear all pumps
    def clear_pumps_widgets(self):
        for widget in self.pumps_frame.winfo_children():
            widget.destroy()
        # destroy the pumps frame
        self.pumps_frame.destroy()
        # recreate pumps frame inside the manual control frame
        self.pumps_frame = ttk.Frame(self.manual_control_frame)
        self.pumps_frame.grid(
            row=1,
            column=0,
            columnspan=5,
            padx=global_pad_x,
            pady=global_pad_y,
            sticky="NSEW",
        )
        self.pumps.clear()

    def update_pump_status(self, response):
        status_pattern = re.compile(
            r"Pump(\d+) Status: Power: (ON|OFF), Direction: (CW|CCW)"
        )
        matches = status_pattern.findall(response)

        for match in matches:
            pump_id, power_status, direction_status = match
            pump_id = int(pump_id)
            if pump_id in self.pumps:
                self.pumps[pump_id]["power_status"] = power_status
                self.pumps[pump_id]["direction_status"] = direction_status
                self.pumps[pump_id]["power_label"].config(
                    text=f"Power Status: {power_status}"
                )
                self.pumps[pump_id]["direction_label"].config(
                    text=f"Direction Status: {direction_status}"
                )
            else:
                # This mean we somehow received a status update for a pump that does not exist
                # clear the pumps widgets and re-query the pump info
                self.clear_pumps_widgets()
                self.query_pump_info()
                logging.error(
                    f"We received a status update for a pump that does not exist: {pump_id}"
                )

    def load_recipe(self):
        file_path = filedialog.askopenfilename(
            initialdir=os.getcwd(),
            title="Select a Recipe File",
            filetypes=(("CSV/Excel files", "*.csv *.xlsx"), ("all files", "*.*")),
        )
        if file_path:
            try:
                # first shutdown the procedure if it is running
                self.stop_procedure()
                # clear the recipe table
                self.clear_recipe()
                if file_path.endswith(".csv"):
                    self.recipe_df = pd.read_csv(
                        file_path, header=None, keep_default_na=False, dtype=object
                    )
                elif file_path.endswith(".xlsx") or file_path.endswith(".xls"):
                    self.recipe_df = pd.read_excel(
                        file_path, header=None, keep_default_na=False, dtype=object
                    )
                elif file_path.endswith(".pkl"):
                    self.recipe_df = pd.read_pickle(file_path, compression=None)
                elif file_path.endswith(".json"):
                    self.recipe_df = pd.read_json(file_path, dtype=False)
                else:
                    raise ValueError("Unsupported file format.")

                # Clean the data frame
                # Search for any cell containing the keyword "time"
                time_cells = [
                    (row_idx, col_idx, cell)
                    for row_idx, row in self.recipe_df.iterrows()
                    for col_idx, cell in enumerate(row)
                    if isinstance(cell, str) and "time" in cell.lower()
                ]
                # we need at least one "time" cell as the anchor
                if len(time_cells) == 0:
                    raise ValueError("No cell containing the keyword 'time'.")
                elif len(time_cells) == 1:
                    # if we only have one "time" cell, we use it as the anchor
                    time_row_idx, time_col_idx, _ = time_cells[0]
                elif len(time_cells) > 1:
                    # Filter to choose the most relevant "Time (min)" cell as the anchor
                    relevant_time_cells = [
                        cell
                        for cell in time_cells
                        if "time (min)" in cell[2].lower()
                        or "time point (min)" in cell[2].lower()
                    ]
                    if len(relevant_time_cells) == 0:
                        raise ValueError(
                            "Multiple cell containing the keyword 'time' found, but none of them contain 'Time (min)' or 'Time point (min)'."
                        )
                    elif len(relevant_time_cells) > 1:
                        raise ValueError(
                            "Multiple cell containing the keyword 'time' found, multiple of them contain 'Time (min)' or 'Time point (min)'."
                        )
                    # Choose the first relevant "Time (min)" cell as the primary one
                    time_row_idx, time_col_idx, _ = relevant_time_cells[0]

                # Trim the DataFrame
                self.recipe_df = self.recipe_df.iloc[time_row_idx:, time_col_idx:]
                # Set the first row as column names
                self.recipe_df.columns = self.recipe_df.iloc[0]
                # Remove the first row
                self.recipe_df = self.recipe_df[1:].reset_index(drop=True)

                # drop rows where "Time point (min)" column has NaN
                self.recipe_df.dropna(subset=[self.recipe_df.columns[0]], inplace=True)
                # drop rows where "Time point (min)" column is empty
                self.recipe_df = self.recipe_df[self.recipe_df.iloc[:, 0] != ""]

                self.recipe_df[self.recipe_df.columns[0]] = self.recipe_df[
                    self.recipe_df.columns[0]
                ].apply(float)

                # check if the time points are in ascending order
                if not self.recipe_df[
                    self.recipe_df.columns[0]
                ].is_monotonic_increasing:
                    raise ValueError(
                        "Time points are required in monotonically increasing order."
                    )

                # check if there is duplicate time points
                if self.recipe_df[self.recipe_df.columns[0]].duplicated().any():
                    raise ValueError("Duplicate time points are not allowed.")

                # Setup the table to display the data
                columns = list(self.recipe_df.columns) + [
                    "Progress Bar",
                    "Remaining Time",
                ]
                self.recipe_table = ttk.Treeview(
                    self.recipe_table_frame, columns=columns, show="headings"
                )

                # Create a scrollbar
                self.scrollbar = ttk.Scrollbar(
                    self.recipe_table_frame,
                    orient="vertical",
                    command=self.recipe_table.yview,
                )
                self.recipe_table.configure(yscrollcommand=self.scrollbar.set)
                self.scrollbar.grid(row=0, column=1, sticky="NS")

                self.recipe_table.grid(
                    row=0, column=0, padx=global_pad_x, pady=global_pad_y, sticky="NSEW"
                )
                for col in columns:
                    self.recipe_table.heading(col, text=col)
                    self.recipe_table.column(col, width=100, anchor="center")

                for index, row in self.recipe_df.iterrows():
                    # Convert all cells to strings, preserving precision for numbers
                    values = [
                        (
                            f"{cell:.15g}"
                            if isinstance(cell, (float, Decimal))
                            else str(cell)
                        )
                        for cell in row
                    ]
                    self.recipe_table.insert("", "end", values=values)
                    self.recipe_rows.append(
                        (index, self.recipe_table.get_children()[-1])
                    )

                # Double width for the notes column if it exists
                if "Notes" in columns:
                    self.recipe_table.column("Notes", width=200, anchor="center")

                # Enable the start button
                self.start_button.config(state=tk.NORMAL)

                logging.info(f"Recipe file loaded successfully: {file_path}")
                self.non_blocking_messagebox(
                    "File Load", f"Recipe file loaded successfully: {file_path}"
                )
            except Exception as e:
                # shutdown the procedure if it is running
                self.stop_procedure()
                self.non_blocking_messagebox(
                    "File Load Error", f"Failed to load recipe file {file_path}: {e}"
                )
                logging.error(f"Error: {e}")

    # a function to clear the recipe table
    def clear_recipe(self):
        try:
            # clear the recipe table
            self.recipe_df = None
            self.recipe_rows = []
            # destroy the recipe table
            self.recipe_table.destroy()
            # destroy the scrollbar
            self.scrollbar.destroy()
            # recreate the recipe table
            self.recipe_table = ttk.Frame(self.recipe_table_frame)
            self.recipe_table.grid(
                row=0, column=0, padx=global_pad_x, pady=global_pad_y, sticky="NSEW"
            )
            # clear the progress bar
            self.total_progress_bar["value"] = 0
            self.remaining_time_value.config(text="")
            self.end_time_value.config(text="")

            # disable all procedure buttons
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.DISABLED)
            self.pause_button.config(state=tk.DISABLED)
            self.continue_button.config(state=tk.DISABLED)
        except Exception as e:
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def start_procedure(self):
        if self.recipe_df is None or self.recipe_df.empty:
            logging.error("No recipe data to execute.")
            return
        # require at least one MCU connection
        if not self.serial_port and not self.serial_port_as:
            self.non_blocking_messagebox(
                "Error", "No MCU connection. Please connect to a MCU to continue."
            )
            return
        # display warning if only one MCU is connected
        if not self.serial_port_as or not self.serial_port:
            message = "Only one MCU connected. Are you sure you want to continue?"
            if not messagebox.askyesno("Warning", message):
                return

        logging.info("Starting procedure...")

        try:
            # enable the stop button
            self.stop_button.config(state=tk.NORMAL)
            # enable the pause button
            self.pause_button.config(state=tk.NORMAL)
            # disable the continue button
            self.continue_button.config(state=tk.DISABLED)
            # disable the disconnect button
            self.disconnect_button.config(state=tk.DISABLED)

            # clear the stop time and pause time
            self.pause_timepoint_ns = -1

            # cancel the scheduled task if it exists
            if self.scheduled_task:
                self.master.after_cancel(self.scheduled_task)
                self.scheduled_task = None

            # calculate the total procedure time
            self.total_procedure_time_ns = self.convert_minutes_to_ns(
                float(self.recipe_df["Time point (min)"].max())
            )

            # clear the "Progress Bar" and "Remaining Time" columns in the recipe table
            for i, child in self.recipe_rows:
                self.recipe_table.set(child, "Progress Bar", "")
                self.recipe_table.set(child, "Remaining Time", "")

            # record start time
            self.start_time_ns = time.monotonic_ns() - self.pause_duration_ns
            self.current_index = 0
            self.execute_procedure()
        except Exception as e:
            # stop the procedure if an error occurs
            self.stop_procedure()
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def execute_procedure(self, index=0):
        if self.recipe_df is None or self.recipe_df.empty:
            self.non_blocking_messagebox("Error", "No recipe file loaded.")
            logging.error("No recipe data to execute.")
            return

        try:
            if index >= len(self.recipe_df):
                # update progress bar and remaining time
                self.update_progress()
                self.start_time_ns = -1
                self.total_procedure_time_ns = -1
                self.current_index = -1
                # call a emergency shutdown in case the power is still on
                self.emergency_shutdown()
                logging.info("Procedure completed.")
                self.non_blocking_messagebox(
                    "Procedure Complete", "The procedure has been completed."
                )
                # disable the stop button
                self.stop_button.config(state=tk.DISABLED)
                self.pause_button.config(state=tk.DISABLED)
                self.continue_button.config(state=tk.DISABLED)
                return

            self.current_index = index
            row = self.recipe_df.iloc[index]
            target_time_ns = self.convert_minutes_to_ns(float(row["Time point (min)"]))

            elapsed_time_ns = (
                time.monotonic_ns() - self.start_time_ns - self.pause_duration_ns
            )
            # calculate the remaining time for the current step
            current_step_remaining_time_ns = target_time_ns - elapsed_time_ns

            # If there is time remaining, sleep for half of the remaining time
            if current_step_remaining_time_ns > 0:
                intended_sleep_time_ms = max(
                    100,
                    current_step_remaining_time_ns // 2 // NANOSECONDS_PER_MILLISECOND,
                )
                # convert from nanoseconds to milliseconds
                self.scheduled_task = self.master.after(
                    int(intended_sleep_time_ms),
                    self.execute_procedure,
                    index,
                )
                return

            logging.info(f"executing step at index {index}")

            # Parse pump and valve actions dynamically
            pump_actions = {
                col: row[col] for col in row.index if col.startswith("Pump")
            }
            valve_actions = {
                col: row[col] for col in row.index if col.startswith("Valve")
            }
            auto_sampler_actions_slots = {
                col: row[col] for col in row.index if col.startswith("Autosampler_slot")
            }
            auto_sampler_actions_positions = {
                col: row[col]
                for col in row.index
                if col.startswith("Autosampler_position")
            }

            # issue a one-time status update
            self.update_status()
            self.execute_actions(
                index,
                pump_actions,
                valve_actions,
                auto_sampler_actions_slots,
                auto_sampler_actions_positions,
            )
        except Exception as e:
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox("Error", f"An error occurred: {e}")

    def execute_actions(
        self,
        index,
        pump_actions,
        valve_actions,
        auto_sampler_actions_slots,
        auto_sampler_actions_positions,
    ):
        for pump, action in pump_actions.items():
            if pd.isna(action) or action == "":
                continue
            match = re.search(r"\d+", pump)
            if match:
                pump_id = int(match.group())
                if (
                    pump_id in self.pumps
                    and action.lower() != self.pumps[pump_id]["power_status"].lower()
                ):
                    logging.debug(
                        f"At index {index}, pump_id {pump_id} status: {self.pumps[pump_id]['power_status']}, intended status: {action}, toggling power."
                    )
                    self.toggle_power(pump_id, update_status=False)

        for valve, action in valve_actions.items():
            if pd.isna(action) or action == "":
                continue
            match = re.search(r"\d+", valve)
            if match:
                valve_id = int(match.group())
                if (
                    valve_id in self.pumps
                    and action.upper()
                    != self.pumps[valve_id]["direction_status"].upper()
                ):
                    logging.debug(
                        f"At index {index}, valve_id {valve_id} status: {self.pumps[valve_id]['direction_status']}, intended status: {action}, toggling direction."
                    )
                    self.toggle_direction(valve_id, update_status=False)

        for _, slot in auto_sampler_actions_slots.items():
            if pd.isna(slot) or slot == "":
                continue
            self.goto_slot_as(str(slot))

        for _, position in auto_sampler_actions_positions.items():
            if pd.isna(position) or position == "":
                continue
            # check if the position is a number
            if position.isdigit():
                self.goto_position_as(int(position))
            else:
                logging.error(
                    f"Warning: Invalid autosampler position: {position} at index {index}"
                )

        # issue a one-time status update
        self.update_status()
        self.execute_procedure(index + 1)

    def update_progress(self):
        if (
            self.total_procedure_time_ns == -1  # Check if not started
            or self.recipe_df is None
            or self.recipe_df.empty
            or self.pause_timepoint_ns != -1  # Check if paused
        ):
            return

        elapsed_time_ns = (
            time.monotonic_ns() - self.start_time_ns - self.pause_duration_ns
        )
        # Handle total_procedure_time_ns being zero
        if self.total_procedure_time_ns <= 0:
            total_progress = 100
            remaining_time_ns = 0
        else:
            total_progress = min(
                100, (elapsed_time_ns / self.total_procedure_time_ns) * 100
            )
            remaining_time_ns = max(
                0,
                self.total_procedure_time_ns - elapsed_time_ns,
            )

        self.total_progress_bar["value"] = int(total_progress)
        self.remaining_time_value.config(
            text=f"{self.convert_ns_to_timestr(int(remaining_time_ns))}"
        )
        end_time = datetime.now() + timedelta(
            seconds=remaining_time_ns / NANOSECONDS_PER_SECOND
        )
        formatted_end_time = end_time.strftime("%Y-%m-%d %a %H:%M:%S")
        self.end_time_value.config(text=f"{formatted_end_time}")

        # Update the recipe table with individual progress and remaining time
        for i, child in self.recipe_rows:
            time_stamp_ns = self.convert_minutes_to_ns(
                float(self.recipe_df.iloc[i]["Time point (min)"])
            )

            # if the time stamp is in the future, break the loop
            if elapsed_time_ns < time_stamp_ns:
                break
            else:
                # Calculate progress for each step
                if i < len(self.recipe_df) - 1:
                    next_row = self.recipe_df.iloc[i + 1]
                    next_time_stamp_ns = self.convert_minutes_to_ns(
                        float(next_row["Time point (min)"])
                    )
                    time_interval = next_time_stamp_ns - time_stamp_ns
                    if time_interval > 0:
                        # handle the case where the next row has the same timestamp
                        row_progress = int(
                            min(
                                100,
                                ((elapsed_time_ns - time_stamp_ns) / time_interval)
                                * 100,
                            )
                        )
                        remaining_time_row_ns = max(
                            0,
                            next_time_stamp_ns - elapsed_time_ns,
                        )
                    else:
                        # If the next row has the same timestamp, mark the progress as 100%
                        row_progress = 100
                        remaining_time_row_ns = 0
                else:
                    row_progress = 100
                    remaining_time_row_ns = 0

                # Update only the "Progress Bar" and "Remaining Time" columns
                self.recipe_table.set(child, "Progress Bar", f"{row_progress}%")
                self.recipe_table.set(
                    child,
                    "Remaining Time",
                    f"{self.convert_ns_to_timestr(int(remaining_time_row_ns))}",
                )

    def convert_minutes_to_ns(self, minutes: float) -> int:
        return int(minutes * 60 * NANOSECONDS_PER_SECOND)

    def convert_ns_to_timestr(self, ns: int) -> str:
        days = ns // NANOSECONDS_PER_DAY
        ns %= NANOSECONDS_PER_DAY
        hours = ns // NANOSECONDS_PER_HOUR
        ns %= NANOSECONDS_PER_HOUR
        minutes = ns // NANOSECONDS_PER_MINUTE
        ns %= NANOSECONDS_PER_MINUTE
        seconds = ns / NANOSECONDS_PER_SECOND

        # Build the formatted time string, hiding fields with 0 values
        time_parts = []
        if days > 0:
            time_parts.append(f"{days} days")
        if hours > 0:
            time_parts.append(f"{hours} hours")
        if minutes > 0:
            time_parts.append(f"{minutes} minutes")
        if seconds > 0:
            time_parts.append(f"{seconds:.1f} seconds")

        return ", ".join(time_parts)

    def add_pump(self):
        # only add a pump if connected to Pico
        if not self.serial_port:
            self.non_blocking_messagebox("Error", "Not connected to Pico.")
            return

        pump_id = len(self.pumps) + 1
        self.add_pump_widgets(
            f"Pump{pump_id} Info: Power Pin: -1, Direction Pin: -1, Initial Power Pin Value: 0, Initial Direction Pin Value: 0, Current Power Status: OFF, Current Direction Status: CCW"
        )

    def edit_pump(self, pump_id):
        pump = self.pumps[pump_id]
        power_pin = simpledialog.askinteger(
            "Power Pin", "Enter power pin ID:", initialvalue=int(pump["power_pin"])
        )
        direction_pin = simpledialog.askinteger(
            "Direction Pin",
            "Enter direction pin ID:",
            initialvalue=int(pump["direction_pin"]),
        )
        initial_power_pin_value = simpledialog.askinteger(
            "Initial Power Pin Value",
            "Enter initial power pin value (0/1):",
            initialvalue=int(pump["initial_power_pin_value"]),
            minvalue=0,
            maxvalue=1,
        )
        initial_direction_pin_value = simpledialog.askinteger(
            "Initial Direction Pin Value",
            "Enter initial direction pin value (0/1):",
            initialvalue=int(pump["initial_direction_pin_value"]),
            minvalue=0,
            maxvalue=1,
        )
        initial_power_status = simpledialog.askstring(
            "Initial Power Status",
            "Enter initial power status (ON/OFF):",
            initialvalue=pump["power_status"],
        )
        initial_direction_status = simpledialog.askstring(
            "Initial Direction Status",
            "Enter initial direction status (CW/CCW):",
            initialvalue=pump["direction_status"],
        )

        if (
            power_pin is not None
            and direction_pin is not None
            and initial_power_pin_value is not None
            and initial_direction_pin_value is not None
            and initial_power_status in ["ON", "OFF"]
            and initial_direction_status in ["CW", "CCW"]
        ):
            self.register_pump(
                pump_id,
                power_pin,
                direction_pin,
                initial_power_pin_value,
                initial_direction_pin_value,
                initial_power_status,
                initial_direction_status,
            )
        else:
            self.non_blocking_messagebox(
                "Error", "Invalid input for pump registration."
            )
        # update the pump info
        self.query_pump_info()

    # A non-blocking messagebox using TopLevel
    def non_blocking_messagebox(self, title, message) -> None:
        try:
            top = tk.Toplevel()
            top.title(title)

            label = ttk.Label(top, text=message)
            label.grid(row=0, column=0, padx=10, pady=10)

            button = ttk.Button(top, text="OK", command=top.destroy)
            button.grid(row=1, column=0, padx=10, pady=10)

            top.geometry(f"+{top.winfo_screenwidth()//2}+{top.winfo_screenheight()//2}")

            top.attributes("-topmost", True)
            top.grab_release()
        except Exception as e:
            logging.error(f"Error: {e}")

    # on closing, minimize the window to the system tray
    def on_closing(self) -> None:
        # pop a message box to confirm exit the first time
        if self.first_close:
            if messagebox.askokcancel(
                "Quit", "Do you want to quit or minimize to tray?"
            ):
                self.first_close = False
                self.exit(icon=None)
            else:
                self.first_close = False
                self.minimize_to_tray_icon()
        else:
            self.minimize_to_tray_icon()
            return

    def exit(self, icon) -> None:
        if icon is not None:
            icon.stop()
        # stop the procedure if it is running
        self.stop_procedure()
        # close the serial ports
        if self.serial_port:
            self.disconnect_pico()
        if self.serial_port_as:
            self.disconnect_pico_as()
        root.quit()

    def show_window(self, icon) -> None:
        icon.stop()
        root.deiconify()

    # A system tray icon which have two menu options: "show window" and "exit", when hovered over the icon, it will display the remaining procedure time if the procedure is running, else display "Pico Controller", the main loop of the program will still be running in the background
    def minimize_to_tray_icon(self) -> None:
        try:
            # hide the window
            root.withdraw()
            menu = (
                pystray.MenuItem("Show", self.show_window),
                pystray.MenuItem("Exit", self.exit),
            )
            icon = pystray.Icon(
                name="Pico EChem Automation Controller",
                icon=self.image_white,
                title="Pico EChem Automation Controller",
                menu=menu,
            )
            icon.run_detached()
        except Exception as e:
            logging.error(f"Error: {e}")
            self.non_blocking_messagebox("Error", f"An error occurred: {e}")


def resource_path(relative_path):
    """Get the absolute path to the resource, works for dev and PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


root = tk.Tk()
root.iconbitmap(resource_path("icons-red.ico"))
app = PicoController(root)
root.protocol("WM_DELETE_WINDOW", app.on_closing)
root.mainloop()
