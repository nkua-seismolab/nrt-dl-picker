# -*- coding: utf-8 -*-
"""
test_api_server.py

A simple built-in HTTP server to test incoming POST requests from
the main script.
"""

import json
import argparse
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

class PickReceiverHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Route validation
        if self.path != "/picks":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            logging.warning(f"Rejected request to invalid path: {self.path}")
            return

        # API Key validation
        provided_key = self.headers.get("X-API-Key")
        if provided_key != self.server.api_key:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"Unauthorized: Invalid API Key")
            logging.warning(f"Rejected request with invalid API key: {provided_key}")
            return

        # Read and parse payload
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        try:
            picks = json.loads(post_data.decode('utf-8'))
            logging.info(f"Received POST /picks with {len(picks)} picks.")
            logging.debug(f"Payload:\n{json.dumps(picks, indent=2)}")
            
            # Send success response
            self.send_response(201)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "received": len(picks)}).encode('utf-8'))
            
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Bad Request: Invalid JSON")
            logging.error("Received invalid JSON payload.")

    def log_message(self, format, *args):
        # Route default HTTP server logs through the standard logging module
        logging.debug(f"{self.address_string()} - {format % args}")


def run():
    parser = argparse.ArgumentParser(description="Dummy API server for testing pick ingestion.")
    parser.add_argument("api_key", help="API key that incoming requests must provide in the X-API-Key header")
    parser.add_argument("--port", type=int, default=8100, help="Port to listen on (default: 8100)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Set the logging level")
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    server_address = ('', args.port)
    httpd = HTTPServer(server_address, PickReceiverHandler)
    httpd.api_key = args.api_key  # handler reads it via self.server

    logging.info(f"Listening for POST requests on http://localhost:{args.port}/picks")
    logging.info(f"Expecting X-API-Key: {args.api_key}")
    logging.info("Press Ctrl+C to stop.")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down server.")
        httpd.server_close()


if __name__ == '__main__':
    run()