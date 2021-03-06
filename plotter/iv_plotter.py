#!/usr/bin/env python
"""Plot data obtained from MQTT broker using Dash."""

import collections
import pickle
import threading
import uuid

import dash
import dash_core_components as dcc
import dash_html_components as html
import numpy as np
import paho.mqtt.client as mqtt
import plotly
import plotly.subplots
import plotly.graph_objs as go


def format_figure_2(data, fig, title="-"):
    """Format figure type 2.

    Parameters
    ----------
    data : array
        Array of data.
    fig : plotly.graph_objs.Figure
        Plotly figure.
    title : str
        Title of plot.

    Returns
    -------
    fig : plotly.graph_objs.Figure
        Updated plotly figure.
    """
    if len(data) == 0:
        # if request to clear has been issued, return cleared figure
        return fig
    else:
        # add data to fig
        fig["data"][0]["x"] = data[:, 0]
        fig["data"][0]["y"] = data[:, 1]

        if np.all(data[:, 2] != np.zeros(len(data[:, 2]))):
            fig["data"][1]["x"] = data[:, 2]
            fig["data"][1]["y"] = data[:, 3]

        # update ranges
        xrange = [
            min(np.append(data[:, 0], data[:, 2])),
            max(np.append(data[:, 0], data[:, 2])),
        ]
        yrange = [
            min(np.append(data[:, 1], data[:, 3])),
            max(np.append(data[:, 1], data[:, 3])),
        ]
        fig["layout"]["xaxis"]["range"] = xrange
        fig["layout"]["yaxis"]["range"] = yrange

        # update title
        fig["layout"]["annotations"][0]["text"] = title

        return fig


# create thread-safe containers for storing latest data and plot info
graph2_latest = collections.deque(maxlen=1)
paused = collections.deque(maxlen=1)
paused.append(False)

# initialise plot info/data queues
graph2_latest.append({"msg": {"clear": True, "idn": "-"}, "data": np.empty((0, 4))})

# initial figure properties
fig2 = plotly.subplots.make_subplots(subplot_titles=["-"])
fig2.add_trace(go.Scatter(x=[], y=[], mode="lines+markers", name="scan0"))
fig2.add_trace(go.Scatter(x=[], y=[], mode="lines+markers", name="scan1"))
fig2.update_xaxes(
    title="bias (V)",
    ticks="inside",
    mirror="ticks",
    linecolor="#444",
    showline=True,
    zeroline=False,
    showgrid=False,
    autorange=False,
)
fig2.update_yaxes(
    title="J (mA/cm^2)",
    ticks="inside",
    mirror="ticks",
    linecolor="#444",
    showline=True,
    zeroline=False,
    showgrid=False,
    autorange=False,
)
fig2.update_layout(
    font={"size": 16}, margin=dict(l=20, r=0, t=30, b=0), plot_bgcolor="rgba(0,0,0,0)"
)

app = dash.Dash(__name__)

app.layout = html.Div(
    html.Div(
        [
            dcc.Graph(id="g2", figure=fig2, style={"width": "95vw", "height": "95vh"}),
            dcc.Interval(id="interval-component", interval=1 * 250, n_intervals=0,),
        ],
    ),
)


@app.callback(
    [dash.dependencies.Output("g2", "figure")],
    [dash.dependencies.Input("interval-component", "n_intervals")],
    [dash.dependencies.State("g2", "figure")],
)
def update_graph_live(n, g2):
    """Update graph."""
    if paused[0] is False:
        g2_latest = graph2_latest[0]

        # update figures
        g2 = format_figure_2(g2_latest["data"], g2, g2_latest["msg"]["idn"])

    return [g2]


def process_iv(payload):
    """Calculate derived I-V parameters.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    """
    data = np.array(payload["data"])
    area = payload["pixel"]["area"]

    # calculate current density in mA/cm2
    j = data[:, 1] * 1000 / area
    p = data[:, 0] * j
    data = np.append(data, j.reshape(len(p), 1), axis=1)
    data = np.append(data, p.reshape(len(p), 1), axis=1)

    # add processed data back into payload to be sent on
    payload["data"] = data.tolist()
    _publish("data/processed/iv_measurement", pickle.dumps(payload))

    return data


def _publish(topic, payload):
    t = threading.Thread(target=_publish_worker, args=(topic, payload,))
    t.start()


def _publish_worker(topic, payload):
    """Publish something over MQTT with a fresh client.

    Parameters
    ----------
    topic : str
        Topic to publish to.
    payload : 
        Serialised payload to publish.
    """
    mqttc = mqtt.Client()
    mqttc.connect(args.mqtthost)
    mqttc.loop_start()
    mqttc.publish(topic, payload, 2).wait_for_publish()
    mqttc.loop_stop()
    mqttc.disconnect()


def read_config(payload):
    """Get config data from payload.

    Parameters
    ----------
    payload : dict
        Request dictionary for measurement server.
    """
    global config

    print("reading config...")

    config = payload["config"]


def on_message(mqttc, obj, msg):
    """Act on an MQTT message."""
    payload = pickle.loads(msg.payload)

    if msg.topic == "data/raw/iv_measurement":
        data = graph2_latest[0]["data"]
        if payload["clear"] is True:
            print("iv clear")
            data = np.empty((0, 4))
        else:
            pdata = process_iv(payload)
            if len(data) == 0:
                data0 = np.array(pdata[:, [0, 4]])
                data1 = np.zeros(data0.shape)
                data = np.append(data0, data1, axis=1)
            else:
                data[:, 2:] = np.array(pdata[:, [0, 4]])
        graph2_latest.append({"msg": payload, "data": data})
    elif msg.topic == "measurement/run":
        read_config(payload)
    elif msg.topic == "plotter/pause":
        print(f"pause: {payload}")
        paused.append(payload)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mqtthost",
        type=str,
        default="127.0.0.1",
        help="IP address or hostname for MQTT broker.",
    )
    parser.add_argument(
        "--dashhost",
        type=str,
        default="127.0.0.1",
        help="IP address or hostname for dash server.",
    )

    args = parser.parse_args()

    # init empty dicts for caching latest data
    config = {}
    eqe_calibration = {}

    # create mqtt client id
    client_id = f"plotter-{uuid.uuid4().hex}"

    mqtt_analyser = mqtt.Client(client_id)
    mqtt_analyser.on_message = on_message

    # connect MQTT client to broker
    mqtt_analyser.connect(args.mqtthost)

    # subscribe to data and request topics
    mqtt_analyser.subscribe("data/raw/iv_measurement", qos=2)
    mqtt_analyser.subscribe("plotter/pause", qos=2)
    mqtt_analyser.subscribe("measurement/run", qos=2)

    print(f"{client_id} connected!")

    mqtt_analyser.loop_start()

    # start dash server
    app.run_server(host=args.dashhost, port=8052, debug=False)
