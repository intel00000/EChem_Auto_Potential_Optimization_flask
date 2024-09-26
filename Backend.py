from flask import Flask, request, jsonify
from threading import Thread, Lock
import time

app = Flask(__name__)

# Global variables to store controllers and their mappings
controllers = {
    "pumps": {},  # Map of {port: PumpController object}
    "autosamplers": {},  # Map of {port: AutosamplerController object}
}
pump_status = {}  # Dictionary mapping global pump_id to status
autosampler_status = {}  # Dictionary mapping global autosampler_id to status
global_pump_id_counter = 1  # Global counter for pump IDs

# Thread lock for safe access to shared resources
lock = Lock()


# Function to assign a global pump ID and track it in pump_status
def register_pump(controller, pump_id_in_controller):
    global global_pump_id_counter
    global_pump_id_counter += 1
    global_pump_id = global_pump_id_counter
    pump_status[global_pump_id] = {
        "controller": controller,
        "local_id": pump_id_in_controller,
    }
    return global_pump_id


# Function to process all commands for all controllers
def process_controllers():
    while True:
        with lock:
            for controller in list(controllers["pumps"].values()) + list(
                controllers["autosamplers"].values()
            ):
                if controller.is_connected():
                    controller.process_all_messages()
        time.sleep(1)


# Background thread to process commands
background_thread = Thread(target=process_controllers, daemon=True)
background_thread.start()


# Endpoint to connect to a PumpController
@app.route("/connect_pump", methods=["POST"])
def connect_pump():
    data = request.get_json()
    port = data.get("port")
    if port in controllers["pumps"]:
        return jsonify(
            {
                "message": f"Pump controller at {port} is already connected",
                "success": False,
            }
        )

    controller_id = len(controllers["pumps"]) + 1
    controller = PumpController(controller_id, port, 1)

    result = controller.connect()

    if result.title == "Success":
        # Register the pumps and assign global IDs
        for local_pump_id in controller.status["pumps_info"]:
            global_pump_id = register_pump(controller, local_pump_id)
            pump_status[global_pump_id]["status"] = controller.status["pumps_info"][
                local_pump_id
            ]

        # Add controller to global port map
        controllers["pumps"][port] = controller

    return jsonify({"message": result.message, "success": result.title == "Success"})


# Endpoint to connect to an AutosamplerController
@app.route("/connect_autosampler", methods=["POST"])
def connect_autosampler():
    data = request.get_json()
    port = data.get("port")
    if port in controllers["autosamplers"]:
        return jsonify(
            {
                "message": f"Autosampler controller at {port} is already connected",
                "success": False,
            }
        )

    controller_id = len(controllers["autosamplers"]) + 1
    controller = AutosamplerController(controller_id, port, 1)

    result = controller.connect()

    if result.title == "Success":
        # Register the autosampler and assign a global ID
        global autosampler_id_counter
        autosampler_id_counter += 1
        autosampler_status[autosampler_id_counter] = {
            "controller": controller,
            "status": controller.status,
        }

        # Add controller to global port map
        controllers["autosamplers"][port] = controller

    return jsonify({"message": result.message, "success": result.title == "Success"})


# Endpoint to disconnect a PumpController by port
@app.route("/disconnect_pump", methods=["POST"])
def disconnect_pump():
    data = request.get_json()
    port = data.get("port")

    with lock:
        controller = controllers["pumps"].get(port)
        if controller:
            result = controller.disconnect()
            if result.title == "Success":
                # Remove the controller from the port map
                del controllers["pumps"][port]
                # Also remove all pumps associated with this controller
                pump_ids_to_remove = [
                    pump_id
                    for pump_id, pump_info in pump_status.items()
                    if pump_info["controller"] == controller
                ]
                for pump_id in pump_ids_to_remove:
                    del pump_status[pump_id]
            return jsonify(
                {"message": result.message, "success": result.title == "Success"}
            )
        return jsonify({"message": "Pump controller not found", "success": False})


# Endpoint to disconnect an AutosamplerController by port
@app.route("/disconnect_autosampler", methods=["POST"])
def disconnect_autosampler():
    data = request.get_json()
    port = data.get("port")

    with lock:
        controller = controllers["autosamplers"].get(port)
        if controller:
            result = controller.disconnect()
            if result.title == "Success":
                # Remove the controller from the port map
                del controllers["autosamplers"][port]
                # Also remove the autosampler from the global status
                autosampler_ids_to_remove = [
                    autosampler_id
                    for autosampler_id, autosampler_info in autosampler_status.items()
                    if autosampler_info["controller"] == controller
                ]
                for autosampler_id in autosampler_ids_to_remove:
                    del autosampler_status[autosampler_id]
            return jsonify(
                {"message": result.message, "success": result.title == "Success"}
            )
        return jsonify(
            {"message": "Autosampler controller not found", "success": False}
        )


# Endpoint to toggle a pump's power
@app.route("/toggle_pump_power/<int:pump_id>", methods=["POST"])
def toggle_pump_power(pump_id):
    with lock:
        pump_info = pump_status.get(pump_id)
        if pump_info:
            controller = pump_info["controller"]
            local_pump_id = pump_info["local_id"]
            controller.toggle_power(local_pump_id)
            return jsonify(
                {"message": f"Toggled power for pump {pump_id}", "success": True}
            )
        return jsonify({"message": "Pump not found", "success": False})


# Endpoint to toggle a pump's direction
@app.route("/toggle_pump_direction/<int:pump_id>", methods=["POST"])
def toggle_pump_direction(pump_id):
    with lock:
        pump_info = pump_status.get(pump_id)
        if pump_info:
            controller = pump_info["controller"]
            local_pump_id = pump_info["local_id"]
            controller.toggle_direction(local_pump_id)
            return jsonify(
                {"message": f"Toggled direction for pump {pump_id}", "success": True}
            )
        return jsonify({"message": "Pump not found", "success": False})


# Endpoint to get the current status of all pumps and autosamplers
@app.route("/get_status", methods=["GET"])
def get_status():
    with lock:
        return jsonify(
            {"pump_status": pump_status, "autosampler_status": autosampler_status}
        )


if __name__ == "__main__":
    app.run(debug=True)
