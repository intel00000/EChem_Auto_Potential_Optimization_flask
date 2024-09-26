import time
import logging
from PumpController import PumpController
from Message import simple_Message

# Set up logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def test_pump_controller():
    # Initialize the pump controller for COM10 with a 2-second timeout
    controller_id = 1
    port_name = "COM10"
    serial_timeout = 2
    pump_controller = PumpController(controller_id, port_name, serial_timeout)

    # print the status dictionary
    print(f"Init Status: {pump_controller.status}")

    # Step 1: Connect to the pump controller
    connect_message = pump_controller.connect()
    print(f"----------------------------------------------------")
    print(f"Connect: {connect_message.title} - {connect_message.message}")
    # print the status dictionary
    if not pump_controller.is_connected():
        print("Failed to connect to the pump controller. Exiting test.")
        return
    
    pump_controller.send_command()
    pump_controller.read_serial(wait=True)
    
    print(f"Step 1: Status: {pump_controller.status}")
    
    # Step 3: Register a new pump with ID 6
    pump_id = 6
    power_pin = 2
    direction_pin = 3
    initial_power_pin_value = 0
    initial_direction_pin_value = 0
    initial_power_status = "OFF"
    initial_direction_status = "CW"

    register_message = pump_controller.register_pump(
        pump_id,
        power_pin,
        direction_pin,
        initial_power_pin_value,
        initial_direction_pin_value,
        initial_power_status,
        initial_direction_status,
    )
    # send the message to the pump controller
    pump_controller.send_command()
    pump_controller.read_serial(wait=True)
    pump_controller.send_command()
    pump_controller.send_command()
    print(f"----------------------------------------------------")
    print(f"Register Pump: {register_message.title} - {register_message.message}")
    # print the status dictionary
    print(f"Step 3: Status: {pump_controller.status}")

    # Step 4: Toggle the pump power
    print(f"----------------------------------------------------")
    print(f"Toggling power for pump {pump_id}...")
    pump_controller.toggle_power(pump_id)
    # send the message to the pump controller
    pump_controller.send_command()
    pump_controller.read_serial(wait=True)
    pump_controller.send_command()
    pump_controller.read_serial(wait=True)
    # print the status dictionary
    print(f"Step 3: Status: {pump_controller.status}")

    # Step 5: Toggle the pump direction
    print(f"----------------------------------------------------")
    print(f"Toggling direction for pump {pump_id}...")
    pump_controller.toggle_direction(pump_id)
    pump_controller.send_command()
    pump_controller.read_serial(wait=True)
    pump_controller.send_command()
    pump_controller.read_serial(wait=True)
    print(f"Status: {pump_controller.status}")

    # Step 6: Save the pump configuration
    save_message = pump_controller.save_config(pump_id)
    print(f"----------------------------------------------------")
    print(f"Save Config: {save_message.title} - {save_message.message}")
    pump_controller.send_command()
    pump_controller.read_serial(wait=True)
    pump_controller.send_command()
    pump_controller.read_serial(wait=True)
    print(f"Status: {pump_controller.status}")

    # Step 7: Remove the newly added pump
    print(f"----------------------------------------------------")
    remove_message = pump_controller.remove_pump(pump_id)
    pump_controller.send_command()
    pump_controller.read_serial(wait=True)
    pump_controller.send_command()
    pump_controller.read_serial(wait=True)
    print(f"Remove Pump: {remove_message.title} - {remove_message.message}")
    print(f"Status: {pump_controller.status}")

    # Step 8: Read any remaining serial messages (if needed)
    read_message = pump_controller.read_serial()
    print(f"----------------------------------------------------")
    print(f"Read Serial: {read_message.title} - {read_message.message}")

    # Step 9: Sync RTC with PC time
    print("Syncing RTC with PC time...")
    print(f"----------------------------------------------------")
    pump_controller.sync_rtc_with_pc_time()
    print(f"Status: {pump_controller.status}")

    # Step 10: Query the RTC time
    print("Querying RTC time...")
    print(f"----------------------------------------------------")
    response = pump_controller.query_rtc_time()
    print(f"RTC Time: {response.title} - {response.message}")
    print(f"Status: {pump_controller.status}")
    
    # Step 12: Shutdown the pump controller
    shutdown_message = pump_controller.shutdown()
    print(f"Shutdown Pico: {shutdown_message.title} - {shutdown_message.message}")
    print(f"Status: {pump_controller.status}")
    
    # Step 13: Disconnect the controller
    disconnect_message = pump_controller.disconnect()
    print(f"Disconnect: {disconnect_message.title} - {disconnect_message.message}")
    print(f"Status: {pump_controller.status}")

# Run the test
test_pump_controller()
