#!/usr/bin/env python3
import os
import re
import sys
import json
import time
import signal
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Tuple, Optional, Any, Dict, Union, Pattern
import requests
import shutil
import subprocess
from server import start_server_in_background, JavaServer
from proxy import start_proxy_in_background, ProxyServer

VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")


def replace_line_in_file(
    filename: Union[str, Path],
    pattern: Union[str, Pattern[str]],
    replacement: str,
    *,
    replace_first: bool = False,
) -> bool:
    file_path = Path(filename)

    lines = file_path.read_text(encoding="utf-8").splitlines(True)

    if isinstance(pattern, str):
        pattern = re.compile(pattern)

    new_lines = []
    replaced = False

    for line in lines:
        if not replaced and pattern.search(line):
            new_lines.append(replacement + ("\n" if line.endswith("\n") else ""))
            replaced = True
            if replace_first:
                new_lines.extend(lines[lines.index(line) + 1:])
                break
        else:
            new_lines.append(line)

    if replaced:
        file_path.write_text("".join(new_lines), encoding="utf-8")

    return replaced


def apply_properties(version: Tuple[int, int, int]):
    data = json.loads(Path("properties.json").read_text(encoding="utf-8"))

    result: Dict[str, Any] = {}
    
    def _walk(node: Any, prefix: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{prefix}{k}.")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{prefix}[{i}].")
        else:
            result[prefix[:-1]] = node

    _walk(data)

    for key in result:
        if key == "difficulty" and version[1] < 14:
            match result[key]:
                case "peaceful":
                    result[key] = 0
                case "easy":
                    result[key] = 1
                case "normal":
                    result[key] = 2
                case "hard":
                    result[key] = 3
        if key == "gamemode" and version[1] < 14:
            match result[key]:
                case "survival":
                    result[key] = 0
                case "creative":
                    result[key] = 1
                case "adventure":
                    result[key] = 2
        value = result[key]
        # Replace setting if it exists
        if isinstance(value, bool):
            value = str(value).lower()
        else:
            value = str(value)
        replace_line_in_file("current/server.properties", rf"\b{key}\b", f"{key}={value}")
    
    return result


def version_to_string(version: Tuple[int, int, int]):
    return f"{version[0]}.{version[1]}.{version[2]}"


def create_version_file():
    if not Path("current.txt").exists():
        Path("current.txt").touch(mode=0o666, exist_ok=True)
        Path("current.txt").write_text("0.0.0\n2000-01-01 00:00:00.000000+00:00", encoding='utf-8')
        print(f"Created current.txt to keep track of currently set version")


def get_version() -> Tuple[int, int, int]:
    create_version_file()
    
    with Path("current.txt").open('r', encoding='utf-8') as f:
        version_str, ts_str = f.read().splitlines()
        return parse_version(version_str)


def get_update_time():
    create_version_file()
    
    with Path("current.txt").open('r', encoding='utf-8') as f:
        version_str, ts_str = f.read().splitlines()
        return datetime.fromisoformat(ts_str)


def parse_version(name: str) -> Optional[Tuple[int, int, int]]:
    match = VERSION_RE.match(name)
    if not match:
        return None

    major, minor, patch = match.group(1), match.group(2), match.group(3)
    patch = patch or "0"
    return int(major), int(minor), int(patch)


def get_versions() -> List[Tuple[int, int, int]]:
    base = "servers"
    items = []
    items.append(parse_version("0.0.0"))
    for entry in os.listdir(base):
        full_path = os.path.join(base, entry)
        if os.path.isdir(full_path):
            ver = parse_version(entry)
            if ver is not None:
                items.append((ver))
    items.sort(key=lambda x: x[1])
    return items


def upgrade_version(server: Optional[JavaServer] = None, proxy: Optional[ProxyServer] = None):
    version = get_version()
    versions = get_versions()
    settings = get_settings()

    if version in versions:
        index = versions.index(version)
        if (index + 1 < len(versions)):
            version = versions[index + 1]
        else:
            print("No later versions are available!")
            return
    else:
        version = versions[0]
    print(f"Upgrading to server version {version_to_string(version)}...")
    if proxy:
        proxy.stop()
    if server:
        server.stop()
    try:
        os.rename("current", "old")
    except PermissionError:
        print("Permission denied – maybe the folder is open in another program.")
        return
    except FileNotFoundError:
        pass
    except OSError as err:
        print(f"Failed to move current folder: {err}")
        return
    
    # Setup the server
    os.makedirs("current", exist_ok=True)
    try:
        shutil.copytree("old/world", "current/world")
    except:
        print("Failed to copy old world or no old world exists")
    shutil.copy(f"servers/{version_to_string(version)}/server.jar", "current/server.jar")

    if not Path("current/eula.txt").exists():
        Path("current/eula.txt").touch(mode=0o666, exist_ok=True)
        Path("current/eula.txt").write_text("eula=true", encoding='utf-8')

    # We run the server for 10 seconds to generate all needed files
    print("Generating configuration files")
    try:
        subprocess.run(["java", f"-Xmx{settings["java-Xmx"]}", f"-Xms{settings["java-Xms"]}", "-jar", "server.jar", "nogui"], timeout=10, cwd="current")
    except subprocess.TimeoutExpired as exc:
        print("Configuration files created")

    apply_properties(version)
    print("Applied server properties")

    Path("current.txt").write_text(f"{version_to_string(version)}\n{datetime.now(timezone.utc)}", encoding='utf-8')
    print(f"Set current version as {version_to_string(version)}!")
    if server:
        server.start()
    if proxy:
        proxy.set_version(version_to_string(version))
        proxy.start()
    discord_message(f"Updated server to version {version_to_string(version)}!")


def get_settings():
    data = json.loads(Path("settings.json").read_text(encoding="utf-8"))

    result: Dict[str, Any] = {}
    
    def _walk(node: Any, prefix: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{prefix}{k}.")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{prefix}[{i}].")
        else:
            result[prefix[:-1]] = node

    _walk(data)
    return result


def check_updates(server: JavaServer, proxy: Optional[ProxyServer] = None):
    settings = get_settings()
    update_time = get_update_time()

    if (update_time + timedelta(days=settings["update_frequency_days"])) > datetime.now(timezone.utc):
        return

    weekday = 0

    match settings["update_weekday"]:
        case "monday":
            weekday = 0
        case "tuesday":
            weekday = 1
        case "wednesday":
            weekday = 2
        case "thursday":
            weekday = 3
        case "friday":
            weekday = 4
        case "saturday":
            weekday = 5
        case "sunday":
            weekday = 6
    
    if datetime.today().weekday() != weekday:
        return

    if settings["update_time_utc"] != datetime.now(timezone.utc).hour:
        return

    # Checks passed, update server
    upgrade_version(server, proxy)


def update_loop(server: JavaServer, proxy: Optional[ProxyServer] = None):
    while True:
        try:
            start = time.time()
            check_updates(server, proxy)
        except Exception as exc:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error: {exc!r}")
        finally:
            elapsed = time.time() - start
            sleep_time = max(60.0 - elapsed, 0.0)
            time.sleep(sleep_time)


def discord_message(message: str):
    webhook_url = get_settings()["discord_webhook_url"]
    if not webhook_url or webhook_url == "":
        return

    payload = {
        "content": message
    }
    headers = {
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(webhook_url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"Failed to send Discord message: {exc}", file=sys.stderr)
        return


def main() -> None:
    settings = get_settings()
    if (version_to_string(get_version()) == "0.0.0"):
        upgrade_version()
    apply_properties(get_version())
    server = start_server_in_background(settings)
    proxy = None
    if (settings["viaproxy-enable"]):
        proxy = start_proxy_in_background(settings, version_to_string(get_version()), apply_properties(get_version())["server-port"])
    # Make sure update_loop() is the last line, as it loops so nothing after this will run
    update_loop(server, proxy)


if __name__ == "__main__":
    main()
