#!/usr/bin/env python3
import curses
import time
import socket
import struct
import subprocess
import psutil
import sys
import glob

REFRESH_SECONDS = 2
DASHBOARD_DURATION = 15   # seconds
GRAPH_DURATION = 45       # seconds

# ----------------------------------------------------------------------
# Existing helper functions (unchanged)
# ----------------------------------------------------------------------
def pick_interfaces(kind):
    ifaces = list(psutil.net_if_addrs().keys())
    kind = kind.lower()
    patterns = []
    if kind == "wifi":
        patterns = ["wlan", "wl"]
    elif kind == "eth":
        patterns = ["eth", "enp", "eno"]
    elif kind == "bt":
        patterns = ["bnep", "bt", "pan"]
    matches = []
    for p in patterns:
        for i in ifaces:
            if p in i.lower() and i not in matches:
                matches.append(i)
    return matches

def get_ip(iface):
    addrs = psutil.net_if_addrs().get(iface, [])
    for a in addrs:
        if getattr(socket, 'AF_INET', None) and a.family == socket.AF_INET:
            return a.address
    return None

def get_mac(iface):
    addrs = psutil.net_if_addrs().get(iface, [])
    for a in addrs:
        if hasattr(socket, 'AF_PACKET') and a.family == socket.AF_PACKET:
            return a.address
        if hasattr(psutil, 'AF_LINK') and a.family == getattr(psutil, 'AF_LINK'):
            return a.address
    return None

def is_up(iface):
    stats = psutil.net_if_stats().get(iface)
    return bool(stats.isup) if stats else False

def get_default_gateway():
    try:
        with open('/proc/net/route', 'r') as f:
            for line in f.readlines()[1:]:
                fields = line.strip().split()
                if len(fields) >= 3:
                    iface, dest, gateway = fields[0], fields[1], fields[2]
                    if dest == '00000000':
                        gw = socket.inet_ntoa(struct.pack('<L', int(gateway, 16)))
                        return gw, iface
    except:
        pass
    return None, None

def get_ssid(iface: str) -> str | None:
    try:
        result = subprocess.run(
            ["iwconfig", iface],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            if "ESSID:" in line:
                essid_part = line.split("ESSID:")[1].strip()
                if essid_part.startswith('"'):
                    end_idx = essid_part.find('"', 1)
                    if end_idx != -1:
                        ssid = essid_part[1:end_idx]
                        return ssid if ssid.lower() != "off/any" else None
                return essid_part.strip('"')
    except FileNotFoundError:
        return None
    except Exception:
        return None

def name_resolution() -> tuple[str | None, str | None, str]:
    dns_ip = None
    test_fqdn = "www.gov.pl"
    test_ip = None

    try:
        with open("/etc/resolv.conf", "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        dns_ip = parts[1]
                        break
    except Exception:
        pass

    if dns_ip:
        try:
            infos = socket.getaddrinfo(test_fqdn, None, socket.AF_INET, socket.SOCK_STREAM)
            if infos:
                test_ip = infos[0][4][0]
        except socket.gaierror:
            pass

    return dns_ip, test_ip, test_fqdn

def ping_ok(host):
    if not host:
        return False
    try:
        res = subprocess.run(["ping", "-c", "1", "-W", "1", host],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except:
        return False

def list_usb_devices():
    devices = []
    for path in glob.glob('/sys/bus/usb/devices/*/product'):
        try:
            with open(path) as f:
                devices.append(f.read().strip())
        except:
            continue
    return devices

def setup_colors():
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, 0)
    curses.init_pair(2, curses.COLOR_RED, 0)
    curses.init_pair(3, curses.COLOR_YELLOW, 0)
    curses.init_pair(4, curses.COLOR_CYAN, 0)
    curses.init_pair(5, curses.COLOR_MAGENTA, 0)

# ----------------------------------------------------------------------
# New functions for Wi‑Fi signal graphing
# ----------------------------------------------------------------------
def get_wifi_signal_quality(iface: str) -> int | None:
    """
    Returns the link quality as a percentage (0-100) for the given Wi‑Fi interface.
    Reads from /proc/net/wireless which is present on most Linux systems with Wi‑Fi.
    Falls back to parsing iwconfig.
    """
    # Try /proc/net/wireless first (fast and reliable)
    try:
        with open('/proc/net/wireless', 'r') as f:
            lines = f.readlines()
        for line in lines[2:]:          # skip header lines
            parts = line.split()
            if len(parts) >= 4 and parts[0].rstrip(':') == iface:
                # Link quality is in the third column, format: "XX."
                quality_str = parts[2].split('.')[0]
                quality = int(quality_str)
                # Usually quality is in range 0-70 (or 0-??). Convert to percentage.
                # Max typical value is 70, but we cap at 100 for safety.
                max_qual = 70
                percent = min(100, int(quality * 100 / max_qual))
                return percent
    except (FileNotFoundError, IndexError, ValueError):
        pass

    # Fallback: parse iwconfig
    try:
        result = subprocess.run(
            ["iwconfig", iface],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if "Link Quality=" in line:
                # Format: "Link Quality=48/70  Signal level=-62 dBm"
                part = line.split("Link Quality=")[1]
                qual_str = part.split()[0]   # e.g. "48/70"
                num, denom = map(int, qual_str.split('/'))
                if denom > 0:
                    percent = int(num * 100 / denom)
                    return percent
    except Exception:
        pass
    return None

def get_active_wifi_interface():
    """Returns the first Wi‑Fi interface that is up and has an SSID."""
    for iface in pick_interfaces("wifi"):
        if is_up(iface) and get_ssid(iface):
            return iface
    return None

def draw_signal_graph(stdscr, duration_seconds):
    """
    Draws a scrolling graph of Wi‑Fi signal quality for the given duration.
    Updates once per second. Exits early if 'q' is pressed.
    """
    curses.curs_set(0)
    stdscr.nodelay(True)
    setup_colors()

    iface = get_active_wifi_interface()
    if not iface:
        # No active Wi‑Fi – show a message and wait briefly, then return
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        msg = "No active Wi‑Fi connection found. Cannot display signal graph."
        stdscr.addstr(h//2, max(0, (w - len(msg))//2), msg, curses.color_pair(3) | curses.A_BOLD)
        stdscr.addstr(h//2 + 2, max(0, (w - 20)//2), "Press any key...")
        stdscr.refresh()
        stdscr.nodelay(False)
        stdscr.getch()
        return

    # Graph settings
    max_history = 60          # enough for 45 seconds (one sample per second)
    history = []              # store percentages
    start_time = time.time()
    end_time = start_time + duration_seconds

    while time.time() < end_time:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        # Title and interface info
        title = f"Wi‑Fi Signal Power Graph – {iface}"
        ssid = get_ssid(iface) or "unknown"
        stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscr.addstr(1, max(0, (w - len(title))//2), title)
        stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscr.addstr(2, 2, f"SSID: {ssid}", curses.color_pair(5))

        # Get current signal quality
        quality = get_wifi_signal_quality(iface)
        if quality is not None:
            history.append(quality)
            if len(history) > max_history:
                history.pop(0)

        # Draw the graph
        if history:
            # Determine available height for the graph (leave room for labels)
            graph_height = min(h - 6, 10)      # at most 10 lines, but adapt to screen
            graph_width = w - 4                # leave margins

            # Normalise history to fit the graph width
            if len(history) > graph_width:
                # Take the most recent values
                plot_data = history[-graph_width:]
            else:
                plot_data = history[:]
                # Pad left with None to align to right side (latest at right)
                plot_data = [None] * (graph_width - len(plot_data)) + plot_data

            # For each column, we will draw a vertical bar using block characters.
            # Map quality (0-100) to height (0 to graph_height)
            for col in range(graph_width):
                q = plot_data[col]
                if q is None:
                    continue
                # Height in rows (0 = bottom, graph_height-1 = top)
                bar_height = int(q * graph_height / 100)
                if bar_height == 0 and q > 0:
                    bar_height = 1   # ensure at least one block if quality>0

                # Draw from the bottom up
                for row in range(bar_height):
                    y = h - 3 - row   # leave a few lines at bottom for axis labels
                    x = 2 + col
                    if 0 <= y < h and 0 <= x < w:
                        # Use full block or lighter block depending on position
                        # For simplicity, use a solid block '#'
                        try:
                            stdscr.addch(y, x, '#', curses.color_pair(1))
                        except curses.error:
                            pass

            # Draw axes and labels
            # Horizontal axis
            axis_y = h - 3 - graph_height
            if axis_y >= 0:
                for x in range(2, 2 + graph_width):
                    try:
                        stdscr.addch(axis_y, x, curses.ACS_HLINE)
                    except curses.error:
                        pass
                # Vertical axis
                for y in range(axis_y, h - 2):
                    try:
                        stdscr.addch(y, 1, curses.ACS_VLINE)
                    except curses.error:
                        pass
                # Corner
                try:
                    stdscr.addch(axis_y, 1, curses.ACS_LTEE)
                except curses.error:
                    pass

            # Labels: 0%, 50%, 100% on vertical axis
            if graph_height >= 2:
                try:
                    stdscr.addstr(axis_y, 0, "100%", curses.A_BOLD)
                    mid_y = axis_y + graph_height // 2
                    stdscr.addstr(mid_y, 0, "50%", curses.A_BOLD)
                    bot_y = h - 3
                    stdscr.addstr(bot_y, 0, "0%", curses.A_BOLD)
                except curses.error:
                    pass

            # Current numeric value
            current_q = history[-1] if history else 0
            stdscr.addstr(h - 2, 2, f"Current signal quality: {current_q}%", curses.color_pair(4))

        else:
            stdscr.addstr(h//2, max(0, (w - 30)//2), "Waiting for signal data...", curses.color_pair(3))

        # Remaining time
        remaining = int(end_time - time.time())
        stdscr.addstr(0, w - 15, f"Time left: {remaining}s", curses.color_pair(5))

        stdscr.refresh()

        # Wait one second, check for quit key
        for _ in range(4):   # 4 * 0.25 = 1 second, responsive to key presses
            time.sleep(0.25)
            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q')):
                return

# ----------------------------------------------------------------------
# Modified dashboard with optional timeout
# ----------------------------------------------------------------------
def draw_dashboard(stdscr, max_duration=None):
    """
    Draws the network status dashboard.
    If max_duration is given (seconds), the function returns after that time.
    Otherwise it runs forever (until 'q' is pressed).
    """
    curses.curs_set(0)
    stdscr.nodelay(True)
    setup_colors()

    start_time = time.time()
    end_time = start_time + max_duration if max_duration else None

    while True:
        # Check timeout
        if end_time is not None and time.time() >= end_time:
            return

        stdscr.erase()
        h, w = stdscr.getmaxyx()

        title = "DA BOX by kmitz6"
        subtitle = f"Refresh every {REFRESH_SECONDS}s"
        stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscr.addstr(2, max(0, 1), title)
        stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscr.addstr(3, max(0, 1), subtitle, curses.color_pair(5))

        sections = [("Wi‑Fi", "wifi"), ("Ethernet", "eth"), ("Bluetooth", "bt")]
        y = 6
        stdscr.addstr(y, 1, "_________", curses.color_pair(4))
        y += 1
        stdscr.addstr(y, 1, "Networks:", curses.A_BOLD | curses.color_pair(4))
        y += 2

        for label, kind in sections:
            ifaces = pick_interfaces(kind)
            stdscr.addstr(y, 2, f"{label}:", curses.A_BOLD | curses.color_pair(4))
            y += 1
            if not ifaces:
                stdscr.addstr(y, 3, "none detected", curses.color_pair(3))
                y += 2
                continue
            for iface in ifaces:
                up = is_up(iface)
                ip = get_ip(iface) or '-'
                mac = get_mac(iface) or '-'
                color = curses.color_pair(1) if up else curses.color_pair(2)

                ssid = None
                if kind == "wifi" and up:
                    ssid = get_ssid(iface)

                stdscr.addstr(y, 3, f"{iface} ", curses.A_BOLD)
                stdscr.addstr("( UP )" if up else "(DOWN)", color | curses.A_BOLD)
                y += 1

                if ssid:
                    stdscr.addstr(y, 5, f"SSID: {ssid}")
                    y += 1

                stdscr.addstr(y, 5, f"IP  : {ip}")
                y += 1
                stdscr.addstr(y, 5, f"MAC : {mac}")
                y += 2

        stdscr.addstr(y, 1, "________________", curses.color_pair(4))
        y += 1
        stdscr.addstr(y, 1, "Name resolution:", curses.A_BOLD | curses.color_pair(4))
        dns_server_ip, dns_result, fqdn_to_test = name_resolution()

        if dns_server_ip:
            y += 2
            stdscr.addstr(y, 2, f"Test   : {fqdn_to_test}")
            y += 1
            stdscr.addstr(y, 2, f"Server : ")
            stdscr.addstr(f"{dns_server_ip}", curses.color_pair(1))
            y += 1
            dns_result_colour = curses.color_pair(1) if dns_result else curses.color_pair(2) | curses.A_BOLD
            stdscr.addstr(y, 2, f"Result : ")
            stdscr.addstr(f"{dns_result}", dns_result_colour)
        else:
            stdscr.addstr("no DNS info", curses.color_pair(3) | curses.A_BOLD)
        y += 2

        stdscr.addstr(y, 1, "_____________", curses.color_pair(4))
        y += 1
        stdscr.addstr(y, 1, "Reachability:", curses.A_BOLD | curses.color_pair(4))
        y += 2
        gw, gw_iface = get_default_gateway()
        tests = [("df gateway", gw), ("quad9 dns", "9.9.9.9"), ("myszka.eu", "myszka.eu"),
                 ("cyfronet.pl", "cyfronet.pl"), ("allegro.pl", "allegro.pl"),
                 ("facebook.com", "facebook.com"), ("youtube.com", "youtube.com")]

        for name, host in tests:
            stdscr.addstr(y, 2, f"{name:<13}: ")
            if not host:
                stdscr.addstr("no default gateway", curses.color_pair(3) | curses.A_BOLD)
            else:
                try:
                    ip_addr = socket.gethostbyname(host)
                except socket.gaierror:
                    ip_addr = "‑"
                ok = ping_ok(host)
                result_str = "UP  " if ok else "DOWN"
                colour = curses.color_pair(1) if ok else curses.color_pair(2) | curses.A_BOLD
                stdscr.addstr(result_str, colour)
                stdscr.addstr(f" {ip_addr}", curses.color_pair(5))
            y += 1

        y += 1
        stdscr.addstr(y, 1, "____________", curses.color_pair(4))
        y += 1
        stdscr.addstr(y, 1, "USB devices:", curses.A_BOLD | curses.color_pair(4))
        y += 2
        usb_devices = list_usb_devices()
        if usb_devices:
            for dev in usb_devices:
                stdscr.addstr(y, 2, dev)
                y += 1
        else:
            stdscr.addstr(y, 4, "none detected", curses.color_pair(3))
            y += 1

        stdscr.refresh()

        # Sleep for REFRESH_SECONDS, but break early if timeout reached
        for _ in range(int(REFRESH_SECONDS * 4)):
            if end_time is not None and time.time() >= end_time:
                return
            time.sleep(0.25)
            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q')):
                return

# ----------------------------------------------------------------------
# Main cycling loop
# ----------------------------------------------------------------------
def run_cycler(stdscr):
    """Alternate between dashboard and signal graph until 'q' is pressed."""
    while True:
        # Show dashboard for DASHBOARD_DURATION seconds
        draw_dashboard(stdscr, max_duration=DASHBOARD_DURATION)
        # Then show signal graph for GRAPH_DURATION seconds
        draw_signal_graph(stdscr, GRAPH_DURATION)

def main():
    try:
        curses.wrapper(run_cycler)
    except KeyboardInterrupt:
        print("Exiting...")
        sys.exit(0)

if __name__ == '__main__':
    main()