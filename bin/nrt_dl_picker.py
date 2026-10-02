# -*- coding: utf-8 -*-
"""
nrt_dl_picker.py

A simple script to fetch waveform data from a SeedLink server in real-time, pick phases 
with a Deep-Learning (DL) picker and post them to the TF2AsLoc API POST /events endpoint.

This script performs the following tasks:
1. Connects to a SeedLink server using ObsPy's easyseedlink to stream real-time data.
2. Buffers the incoming data and extracts sequential sliding windows of length `t` 
   every `n` seconds.
3. Pre-processes the window (detrending, merging, and zero-padding).
4. Applies a configured SeisBench DL model (e.g., EQTransformer) to detect picks.
5. Maps picks to the TF2AsLoc data model and POSTs them as a JSON payload 
   to the /picks endpoint.

For more information, please see https://github.com/nkua-seismolab/nrt-dl-picker
"""

import sys
import argparse
import yaml
import json
import time
import threading
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from obspy import UTCDateTime, Stream
from obspy.clients.seedlink.easyseedlink import EasySeedLinkClient
from obspy.clients.seedlink.basic_client import Client as BasicClient
import seisbench.models as sbm

__version__ = "1.0.0"


class SeedlinkBuffer:
    """
    A thread-safe buffer that listens to a SeedLink server and accumulates waveform data.
    """
    def __init__(self, host, port, network, station, channel):
        self.stream = Stream()
        self.lock = threading.Lock()
        
        server_url = f"{host}:{port}"
        
        ## Initialize the SeedLink client with a callback for incoming data
        # Split comma-separated strings and subscribe to each combination
        networks = [n.strip() for n in network.split(',')]
        stations = [s.strip() for s in station.split(',')]
        channels = [c.strip() for c in channel.split(',')]

        # Get eligible stream codes from SeedLink server
        seed_streams = self.get_streams(server_url,networks, stations, channels)

        if not seed_streams:
            logging.error("No streams found matching the specified criteria.")
            sys.exit(1)

        # Setup the SeedLink client with callbacks.
        # Built manually (instead of easyseedlink.create_client) so a
        # connection timeout can be set before connecting; obspy >= 1.5
        # leaves it as None, which crashes EasySeedLinkClient.connect().
        self.client = EasySeedLinkClient(server_url, autoconnect=False)
        if self.client.conn.timeout is None:
            self.client.conn.timeout = 30
        self.client.on_data = self.on_data
        # self.client.on_seedlink_error = self.on_error  # PENDING
        self.client.on_terminate = self.on_terminate
        self.client.connect()
        for seed in seed_streams:
            net, sta, _, cha = seed
            self.client.select_stream(
                net=net,
                station=sta,
                selector=cha
            )
        
        # Run the SeedLink client in a background daemon thread
        self.thread = threading.Thread(target=self.client.run, daemon=True)
        self.thread.start()

    def get_streams(self, server_url, networks="*", stations="*", channels="*"):
        """
        Returns a list with available stream IDs according to
        filter criteria.
        """
        logging.info(f"Fetching available streams from {server_url}...")
        logging.info(f"Filter criteria - Networks: {networks}, Stations: {stations}, Channels: {channels}")
        
        # Connect to base client
        server, port = server_url.split(":")
        info_client = BasicClient(server=server, port=int(port))
        
        # Get all available streams
        all_streams = info_client.get_info(level='channel')
        
        # Filter by user-specified networks and stations.
        filtered_streams = []
        for stream in all_streams:
            net, sta, loc, cha = stream
            if not "*" in networks and not net in networks:
                continue
            if not "*" in stations and not sta in stations:
                continue
            if not "*" in channels and not cha[:2] + "?" in channels:
                continue
            filtered_streams.append(stream)
        logging.info(f"Found {len(filtered_streams)} streams matching criteria.")
        return filtered_streams

    def on_error(self, error):
        """Callback function triggered on SeedLink errors."""
        logging.error(f"SeedLink error: {error}")

    def on_terminate(self):
        """Callback function triggered when the SeedLink client terminates."""
        logging.warning("SeedLink client terminated.")
        sys.exit(1)

    def on_data(self, trace):
        """Callback function triggered when a new SeedLink packet arrives."""
        with self.lock:
            self.stream.append(trace)

    def get_window(self, start_time, end_time):
        """
        Safely extracts a time window from the buffer and clears older, unneeded data 
        to prevent memory leaks.
        """
        with self.lock:
            # Losslessly merge contiguous packets so the buffer doesn't hold
            # thousands of tiny trace fragments (slows slice/trim over time)
            self.stream._cleanup()

            # Create a copy of the required time segment
            st_window = self.stream.slice(starttime=start_time, endtime=end_time).copy()
            
            # Trim to suitable time window with +60 s buffer to account for late-arriving data
            self.stream.trim(starttime=start_time, endtime=UTCDateTime() + 60)
        return st_window


def process_stream(st, start_time, end_time):
    """
    Pre-processes the waveform stream for the DLmodel.
    Applies detrending, merging to fill data gaps with zeros, and exact padding.
    """
    if not st:
        return st
    
    # Detrend before merging/padding to avoid step artifacts
    st.detrend('linear').detrend('demean')
    
    # Merge traces with identical IDs, filling gaps with zeros
    st.merge(method=1, fill_value=0)
    
    # Pad exact window using the user-specified expected bounds, not the waveform start/end
    st.trim(starttime=start_time, endtime=end_time, pad=True, fill_value=0)
    return st


def post_picks(picks, url, api_key):
    """
    POSTs a list of pick dictionaries to the target API endpoint as JSON.
    """
    if not picks:
        return
        
    body = json.dumps(picks).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    
    try:
        with urllib.request.urlopen(req) as resp:
            logging.info(f"POSTed {len(picks)} picks. Status: {resp.status}")
    except Exception as e:
        logging.error(f"Failed to POST picks: {e}")


def main():
    """Main execution loop for the real-time picker."""
    parser = argparse.ArgumentParser(description="Real-time SeisBench picker via SeedLink.")
    parser.add_argument("config", help="Path to YAML configuration file.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    # Get configuration from a YAML
    with open(args.config, "r") as f:
        config = yaml.safe_load(f)

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, str(config.get('log_level', 'INFO')).upper()),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    logging.info(f"Starting nrt-dl-picker v{__version__}...")

    # Load the SeisBench Model
    logging.info(f"Loading {config['model']['name']}...")
    try:
        model = getattr(sbm, config['model']['name']).from_pretrained(config['model']['weights'])
        model.to_preferred_device()
    except Exception as e:
        logging.error(f"Failed to load model: {e}")
        sys.exit(1)
    
    # Listen to a SEEDLINK server
    sl_config = config['seedlink']
    logging.info(f"Connecting to SeedLink server at {sl_config['host']}:{sl_config['port']}...")
    buffer = SeedlinkBuffer(
        sl_config['host'], sl_config['port'], 
        sl_config['network'], sl_config['station'], sl_config['channel']
    )

    t_length = config['window']['length_s']
    t_step = config['window']['step_s']
    api_url = config['api']['url']
    api_key = config['api'].get('key')
    if not api_key:
        logging.error("No API key provided. Set api.key in the config file.")
        sys.exit(1)

    # Setup cross-window deduplication: skip picks within tolerance_s 
    # of one already sent, for the same station and phase combo
    dedup_cfg = config.get('dedup', {})
    dedup_enabled = dedup_cfg.get('enabled', False)
    dedup_tolerance = float(dedup_cfg.get('tolerance_s', 2.0))
    sent_picks = {}  # (trace_id, phase) -> list of sent peak times (UTCDateTime)

    # Wait for the initial buffer window to fill up before processing
    logging.info(f"Buffering initial {t_length} seconds of data...")
    time.sleep(t_length)
    
    # Initialize timing for the sliding window
    current_time = UTCDateTime() - t_length
    logging.info("Starting processing loop...")
    
    while True:
        window_start = current_time
        window_end = window_start + t_length
        logging.debug(f"Processing window: {window_start} to {window_end}")
        
        # Retrieve and process the current window
        st_raw = buffer.get_window(window_start, window_end)
        st_proc = process_stream(st_raw, window_start, window_end)
        
        if st_proc:
            # Apply the selected DL model to the processed stream
            res = model.classify(st_proc)
            raw_picks = res.picks
            logging.debug(f"Detected {len(raw_picks)} picks in current window.")

            # for each phase type (p, s) we need to select the
            # pick with the maximum probability in this window
            trace_ids = set(p.trace_id for p in raw_picks)
            p_picks = []
            s_picks = []
            for trace_id in trace_ids:
                p_pick = max((p for p in raw_picks if p.phase == 'P' and p.trace_id == trace_id), default=None, key=lambda x: x.peak_value)
                s_pick = max((p for p in raw_picks if p.phase == 'S' and p.trace_id == trace_id), default=None, key=lambda x: x.peak_value)
                if p_pick:
                    p_picks.append(p_pick)
                if s_pick:
                    s_picks.append(s_pick)

            selected_picks = p_picks + s_picks
            if dedup_enabled:
                # Drop sent times too old to ever match again, so memory stays bounded
                horizon = window_start - dedup_tolerance
                for key in list(sent_picks):
                    sent_picks[key] = [t for t in sent_picks[key] if t >= horizon]
                    if not sent_picks[key]:
                        del sent_picks[key]

                deduped = []
                for p in selected_picks:
                    key = (p.trace_id, p.phase)
                    times = sent_picks.setdefault(key, [])
                    if any(abs(p.peak_time - t) <= dedup_tolerance for t in times):
                        continue
                    times.append(p.peak_time)
                    deduped.append(p)
                if len(deduped) < len(selected_picks):
                    logging.debug(f"Deduplication skipped {len(selected_picks) - len(deduped)} already-sent picks.")
                selected_picks = deduped

            payload = []
            for p in selected_picks:
                ## Map SeisBench output to the TF2AsLoc picks data model.                
                # Split trace_id (e.g., "IU.KBS.00.BHZ") into components safely
                parts = p.trace_id.split('.')

                network = parts[0] if len(parts) > 0 else ""
                station = parts[1] if len(parts) > 1 else ""
                location = parts[2] if len(parts) > 2 else ""
                channel = parts[3] if len(parts) > 3 else ""

                payload.append({
                    "network": network,
                    "station": station,
                    "location": location,
                    "channel": channel,
                    "phase": p.phase,
                    "time": p.peak_time.datetime.isoformat() + "Z",
                    "prob": float(p.peak_value),
                    "model": config['model']['name'],
                    "author": config['metadata']['author'],
                })
            
            # Send the JSON payload via HTTP POST
            post_picks(payload, api_url, api_key)

        # Advance the window boundary by 'n' seconds
        current_time += t_step
        
        # Calculate exactly how long to sleep to maintain the step cadence,
        # accounting for processing time
        now = UTCDateTime()
        next_iter = current_time + t_length
        sleep_time = next_iter - now
        if sleep_time > 0:
            logging.debug(f"Sleeping for {sleep_time:.2f} seconds to maintain cadence.")
            time.sleep(sleep_time)
        elif sleep_time < -t_length:
            # Processing cannot keep up with real time; skip ahead so the buffer
            # does not grow without bound while we chase an ever-growing backlog.
            new_time = now - t_length
            logging.warning(
                f"Processing lagging real time by {-sleep_time:.1f}s; "
                f"skipping ahead {new_time - current_time:.1f}s of data."
            )
            current_time = new_time


if __name__ == "__main__":
    main()