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

class JavaServer:
    def __init__(
        self,
        settings: Dict[str, Any],
        cwd: str | None = None,
        restart_delay: float = 5.0,
        check: bool = True,
    ) -> None:
        self.settings = settings
        self.cwd = cwd or "current"
        self.restart_delay = restart_delay
        self.check = check

        self._stop_event = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logging.warning("Server thread already running")
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="JavaServerThread",
            daemon=True,
        )
        self._thread.start()
        logging.info("JavaServer thread started")

    def stop(self, timeout: float | None = 10.0) -> None:
        logging.info("Stopping Java server …")
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

    def _run_loop(self) -> None:
        cmd = [
            "java",
            f"-Xmx{self.settings['java-Xmx']}",
            f"-Xms{self.settings['java-Xms']}",
            "-jar",
            "server.jar",
            "nogui",
        ]

        while not self._stop_event.is_set():
            try:
                logging.info("Launching JVM: %s", " ".join(cmd))
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=self.cwd,
                )

                while True:
                    if self._stop_event.is_set():
                        logging.info("Stop requested – shutting down JVM")
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
                    logging.info("JVM exited cleanly (code 0) – restarting")
                else:
                    logging.warning("JVM crashed (exit code %s) – restarting", ret)

                time.sleep(self.restart_delay)

            except Exception as exc:
                logging.exception("Unexpected error while running JVM: %s", exc)
                time.sleep(self.restart_delay)

def start_server_in_background(settings: Dict[str, Any]) -> JavaServer:
    server = JavaServer(settings)
    server.start()
    return server
