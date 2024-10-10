import asyncio
import serial.tools.list_ports
from multiprocessing import Manager, Lock
import logging
from AutosamplerController import AutosamplerController

# Define Pi Pico vendor ID
pico_vid = 0x2E8A


# Set up logging
def setup_logging():
    """Configure logging for the program and AutosamplerController."""
    logger = logging.getLogger("AutosamplerController")
    logger.setLevel(logging.DEBUG)  # Set logging level to DEBUG to capture all logs

    # Create console handler to output logs to console
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)

    # Create a formatter and attach it to the handler
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s [%(funcName)s]"
    )
    ch.setFormatter(formatter)

    # Add the handler to the logger
    logger.addHandler(ch)

    return logger


def list_pico_ports():
    """List all available COM ports and filter for Pi Pico devices."""
    ports = serial.tools.list_ports.comports()
    pico_ports = [port.device for port in ports if port.vid == pico_vid]

    if not pico_ports:
        print("No Pi Pico devices found.")
    else:
        print("Available Pi Pico ports:")
        for port in pico_ports:
            print(f"  - {port}")

    return pico_ports


async def main():
    # Set up logging
    logger = setup_logging()

    # List available Pi Pico COM ports
    pico_ports = list_pico_ports()

    if not pico_ports:
        print("No Pi Pico devices available. Exiting.")
        return

    # Prompt the user to select a COM port
    com_port = input(
        f"Enter the COM port for the Autosampler (available: {', '.join(pico_ports)}): "
    )

    if com_port not in pico_ports:
        print(
            f"Invalid COM port. Please choose one from the list: {', '.join(pico_ports)}"
        )
        return

    # Initialize the manager and lock
    manager = Manager()
    lock = Lock()

    # Create the AutosamplerController object with the specified COM port, logger, and a timeout of 1 second
    controller = AutosamplerController(
        controller_id=1,
        port_name=com_port,
        serial_timeout=1,
        lock=lock,
        manager=manager,
        logger=logger,  # Pass the logger to the controller
    )

    # Show available commands with numbers
    print(
        """
    Available commands:
    1. Connect to the autosampler.
    2. Disconnect from the autosampler.
    3. Go to a specific position.
    4. Go to a specific slot.
    5. Query the autosampler status.
    6. Query the RTC time.
    7. Query the slot configuration.
    8. Add a new slot.
    9. Remove a slot.
    10. Move one step left or right.
    11. Save the configuration.
    12. Print the status dictionary.
    13. Exit the program.
    """
    )

    while True:
        try:
            # Take user input for a command number
            command = input("Enter a command number: ")

            if command == "1":  # Connect
                result = await controller.connect()
                print(result)

            elif command == "2":  # Disconnect
                result = await controller.disconnect()
                print(result)

            elif command == "3":  # Go to a specific position
                position = input("Enter the position: ")
                await controller.goto_position(position)

            elif command == "4":  # Go to a specific slot
                slot = input("Enter the slot: ")
                await controller.goto_slot(slot)

            elif command == "5":  # Query the status
                await controller.query_status()

            elif command == "6":  # Query RTC time
                await controller.query_rtc_time()

            elif command == "7":  # Query slot configuration
                await controller.query_config()

            elif command == "8":  # Add a new slot
                slot_name = input("Enter the slot name: ")
                slot_position = input("Enter the slot position: ")
                await controller.add_slot(slot_name, slot_position)

            elif command == "9":  # Remove a slot
                slot_name = input("Enter the slot name to remove: ")
                await controller.remove_slot(slot_name)

            elif command == "10":  # Move one step left or right
                direction = input("Enter direction (left or right): ").lower()
                if direction in ["left", "right"]:
                    await controller.move_one_step(direction)
                else:
                    print("Invalid direction. Please enter 'left' or 'right'.")

            elif command == "11":  # Save the configuration
                await controller.save_config()

            elif command == "12":  # Print status dictionary
                with controller.lock:
                    print("Status Dictionary:")
                    for key, value in controller.status.items():
                        print(f"{key}: {value}")

            elif command == "13":  # Exit
                print("Exiting...")
                break

            else:
                print("Invalid command. Please enter a number from 1 to 13.")

        except Exception as e:
            print(f"Error: {e}")


# Run the program
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program terminated.")
