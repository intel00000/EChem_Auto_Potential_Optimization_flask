from flask import Flask, jsonify, render_template
import time

app = Flask(__name__)


@app.route("/")
def index():
    # Render the HTML page for latency testing
    return render_template("latency_test.html")


@app.route("/ping", methods=["GET"])
def ping():
    # Return the current server time
    return jsonify({"message": "pong", "server_time": time.time()})


if __name__ == "__main__":
    app.run(debug=True)
