# app.py

from flask import Flask, render_template, request, jsonify
from backend import pico_controller  # Import the PicoController instance
import threading

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/get_ports")
def get_ports():
    ports = pico_controller.get_available_ports()
    return jsonify({"ports": ports})


@app.route("/connect_pico", methods=["POST"])
def connect_pico():
    data = request.get_json()
    port = data.get("port")
    if not port:
        return jsonify({"success": False, "message": "No port specified"})

    success = pico_controller.connect_to_pico(port)
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "Failed to connect to Pico"})


@app.route("/disconnect_pico", methods=["POST"])
def disconnect_pico():
    pico_controller.disconnect_pico()
    return jsonify({"success": True})


@app.route("/reset_pico", methods=["POST"])
def reset_pico():
    success = pico_controller.reset_pico()
    if success:
        return jsonify({"success": True, "message": "Reset command sent to Pico"})
    else:
        return jsonify({"success": False, "message": "Pico not connected"})


# Similar routes for Autosampler
@app.route("/connect_pico_as", methods=["POST"])
def connect_pico_as():
    data = request.get_json()
    port = data.get("port")
    if not port:
        return jsonify({"success": False, "message": "No port specified"})

    success = pico_controller.connect_to_pico_as(port)
    if success:
        return jsonify({"success": True})
    else:
        return jsonify(
            {"success": False, "message": "Failed to connect to Autosampler"}
        )


@app.route("/disconnect_pico_as", methods=["POST"])
def disconnect_pico_as():
    pico_controller.disconnect_pico_as()
    return jsonify({"success": True})


@app.route("/reset_pico_as", methods=["POST"])
def reset_pico_as():
    # Implement reset for Autosampler
    pass  # Similar to reset_pico()


# Other routes as needed

if __name__ == "__main__":
    app.run(debug=True)
