# Near-Real-Time Deep-Learning Picker
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)

Near-real-time seismic phase picker. Streams waveform data from a SeedLink server, runs a SeisBench deep learning model (e.g., PhaseNet, EQTransformer) on sliding windows, and POSTs the resulting picks as JSON to the TF2AsLoc API.

This utility is released for use with the TRANSFORM² Associator-Locator software.

Models are loaded directly from [SeisBench](https://seisbench.readthedocs.io/en/stable/pages/models/overview.html), which means that you may use whichever model is available in the installed version (as long as model and weights names are configured). However, the script expects inferred picks be marked as either 'p' or 's' - otherwise they are ignored.

## Install

Requires Python 3.12. Using a dedicated environment (e.g., venv and uv or Conda) is recommended.

```bash
pip install -r requirements.txt
```

Models are sent to the preferred device after loading. If you have a compatible GPU and the Torch packages are setup properly, picks will be inferred using the GPU.

## Configure

Copy the example config and edit it:

```bash
cp config.example.yaml config.yaml
```

```yaml
seedlink:
  host: "seedlink.server"
  port: 18000
  network: "*"
  station: "*"
  channel: "*"
window:
  length_s: 30   # window length in seconds
  step_s: 10     # step between windows in seconds
dedup:
  enabled: true  # skip picks already sent in previous (overlapping) windows
  tolerance_s: 1.0
model:
  name: "phasenet"
  weights: "original"
api:
  url: "http://localhost:8100/picks"
  key: "your-api-key-here"
metadata:
  author: "your-agency-id"
log_level: "INFO"
```

Note on deduplication: picks are POSTed as soon as they are first detected. If a later overlapping window re-detects the same arrival with a higher probability, it is dropped - the first detection wins.

## Run

```bash
python bin/nrt_dl_picker.py config.yaml
```

## Testing

You can test the picker end-to-end without a running TF2AsLoc instance by using the dummy API server in `tests/`. It accepts `POST /picks` requests, validates the API key, and logs the received payloads.

Start the test server with the API key that clients must present (and optionally a port, default 8100):

```bash
python tests/test_api_server.py my-test-key --port 8100
```

Point the picker at it by setting the matching values in `config.yaml`:

```yaml
api:
  url: "http://localhost:8100/picks"
  key: "my-test-key"
```

Then run the picker as usual. Each posted batch appears in the test server log; run it with `--log-level DEBUG` to print the full JSON payloads. Requests with a wrong key are rejected with `401`, which lets you also verify the key handling.

## Run as a systemd service

Create `/etc/systemd/system/nrt-dl-picker.service`:

```ini
[Unit]
Description=NRT deep learning seismic picker
After=network-online.target
Wants=network-online.target

[Service]
User=your_user
WorkingDirectory=your-install-path/nrt-dl-picker
ExecStart=your-install-path/.venv/bin/python bin/nrt_dl_picker.py config.yaml
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Adjust `User`, paths, and the Python interpreter to your setup, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nrt-dl-picker
```

Check status and logs:

```bash
systemctl status nrt-dl-picker
journalctl -u nrt-dl-picker -f  # logs in real-time
```

## License

This software is released under the [GNU General Public License v3.0](LICENSE).


## Contact

Ioannis Spingos, Ph.D.  
email: ispingos@geol.uoa.gr

## How to cite

A citation will be provided soon.

If you use this script, please make sure to also cite SeisBench [(Woollam et al., 2002)](https://doi.org/10.1785/0220210324) and the DL model used.

## Funding

This work is part of the [TRANSFORM²](https://www.transform2-project.eu/) project which aims to improve physical and digital infrastructure across Near-Fault Observatories (NFOs) in Europe.

TRANSFORM² is funded by the European Union under project number 101188365 within the HORIZON-INFRA-2024-DEV-01-01 call.

<div align="center">
  <img src="https://www.transform2-project.eu/wp-content/uploads/2022/08/Logo_TRANSFORM2-round-logo-100x100-1.png" alt="TRANSFORM² logo">
</div>