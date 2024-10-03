import asyncio
import serial.tools.list_ports
from multiprocessing import Manager, Lock
import logging
from AutosamplerController import (
    AutosamplerController,
)  # Assuming this is in the same directory/module

# Define Pi Pico vendor ID
pico_vid = 0x2E8A

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


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

    # Create the AutosamplerController object with the specified COM port and a timeout of 1 second
    controller = AutosamplerController(
        controller_id=1,
        port_name=com_port,
        serial_timeout=1,
        lock=lock,
        manager=manager,
    )

    # Show available commands
    print(
        """
    Available commands:
    1. connect - Connect to the autosampler.
    2. disconnect - Disconnect from the autosampler.
    3. goto_position [position] - Move to a specific position.
    4. goto_slot [slot] - Move to a specific slot.
    5. query_status - Query the autosampler status.
    6. query_rtc - Query the RTC time.
    7. query_config - Query the slot configuration.
    8. exit - Exit the program.
    """
    )

    while True:
        # Take user input
        command = input("Enter a command: ")

        # Split the input into command and argument (if any)
        parts = command.split()

        if len(parts) == 0:
            continue

        cmd = parts[0]

        if cmd == "connect":
            result = await controller.connect()
            print(result)

        elif cmd == "disconnect":
            result = await controller.disconnect()
            print(result)

        elif cmd == "goto_position":
            if len(parts) == 2:
                position = parts[1]
                await controller.goto_position(position)
            else:
                print("Usage: goto_position [position]")

        elif cmd == "goto_slot":
            if len(parts) == 2:
                slot = parts[1]
                await controller.goto_slot(slot)
            else:
                print("Usage: goto_slot [slot]")

        elif cmd == "query_status":
            await controller.query_status()

        elif cmd == "query_rtc":
            await controller.query_rtc_time()

        elif cmd == "query_config":
            await controller.query_config()

        elif cmd == "exit":
            print("Exiting...")
            break

        else:
            print("Invalid command. Please try again.")


# Run the program
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program terminated.")
