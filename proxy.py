#!/usr/bin/env python3
import subprocess
import threading
import time
import logging
from typing import Dict, Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
    datefmt="%H:%M:%S",
)

# This module provides a ProxyServer class that manages the lifecycle of a ViaProxy server process, which acts as a proxy for Minecraft servers. It includes methods to start and stop the proxy, as well as a main loop that restarts the proxy if it crashes. The proxy is configured to allow legacy client passthrough and to bind to a specified address and port, forwarding traffic to a target Minecraft server.
class ProxyServer:
    def __init__(
        self,
        settings: Dict[str, Any],
        version: str,
        port: int,
        cwd: str = "viaproxy/",
        restart_delay: float = 5.0,
    ) -> None:
        self.settings = settings
        self.version = version
        self.port = port
        self.cwd = cwd
        self.restart_delay = restart_delay

        self._stop_event = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    # Start the proxy server in a background thread. If the proxy is already running, this does nothing.
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logging.warning("ProxyServer thread already running")
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="ProxyServerThread",
            daemon=True,
        )
        self._thread.start()
        logging.info("ProxyServer thread started")

    # Stop the proxy server and wait for it to exit, with a timeout. If the JVM does not exit gracefully, kill it.
    def stop(self, timeout: float | None = 10.0) -> None:
        logging.info("Stopping Proxy server …")
        self._stop_event.set()

        if self._proc and self._proc.poll() is None:
            logging.info("Terminating JVM …")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
                logging.info("JVM exited gracefully")
            except subprocess.TimeoutExpired:
                logging.warning("JVM did not exit in time – killing")
                self._proc.kill()
                self._proc.wait()

        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logging.warning("Thread still alive after %s seconds", timeout)
            else:
                logging.info("Thread finished cleanly")

        self._thread = None
        self._proc = None
        self._stop_event.clear()

    # Set the Minecraft version to proxy for. This will be used in the command line arguments when launching ViaProxy.
    def set_version(self, ver: str):
      self.version = ver

    # The main loop that runs the ViaProxy server process and restarts it if it crashes. It constructs the command line arguments based on the settings and version, launches the process, and monitors it for crashes or stop requests.
    def _run_loop(self) -> None:
        version = self.version

        if (version).endswith('.0'):
            version = version[:-2]

        cmd = [
            "java",
            f"-Xmx{self.settings['viaproxy-java-Xmx']}",
            f"-Xms{self.settings['viaproxy-java-Xms']}",
            "-jar",
            "ViaProxy.jar",
            "cli",
            "--allow-legacy-client-passthrough",
            "true",
            "--proxy-online-mode",
            "true",
            "--bind-address",
            f"0.0.0.0:{self.settings['viaproxy-port']}",
            "--target-address",
            f"127.0.0.1:{self.port}",
            "--target-version",
            version,
        ]

        while not self._stop_event.is_set():
            try:
                logging.info("Launching ViaProxy: %s", " ".join(cmd))
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=self.cwd,
                )

                while True:
                    if self._stop_event.is_set():
                        logging.info("Stop requested – shutting down ViaProxy")
                        self._proc.terminate()
                        self._proc.wait()
                        return

                    ret = self._proc.poll()
                    if ret is not None:
                        break

                    time.sleep(0.5)

                if self._stop_event.is_set():
                    return

                if ret == 0:
                    logging.info("ViaProxy exited cleanly (code 0) – restarting")
                else:
                    logging.warning("ViaProxy crashed (exit code %s) – restarting", ret)

                time.sleep(self.restart_delay)

            except Exception as exc:
                logging.exception("Unexpected error while running ViaProxy: %s", exc)
                time.sleep(self.restart_delay)

# Helper function to start the proxy server in the background and return the ProxyServer instance.
def start_proxy_in_background(settings: Dict[str, Any], version: str, port: int) -> ProxyServer:
    proxy = ProxyServer(settings, version, port)
    proxy.start()
    return proxy
