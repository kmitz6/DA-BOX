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
# Wi‑Fi signal quality (unchanged)
# ----------------------------------------------------------------------
def get_wifi_signal_quality(iface: str) -> int | None:
    try:
        with open('/proc/net/wireless', 'r') as f:
            lines = f.readlines()
        for line in lines[2:]:
            parts = line.split()
            if len(parts) >= 4 and parts[0].rstrip(':') == iface:
                quality_str = parts[2].split('.')[0]
                quality = int(quality_str)
                max_qual = 70
                percent = min(100, int(quality * 100 / max_qual))
                return percent
    except (FileNotFoundError, IndexError, ValueError):
        pass

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
                part = line.split("Link Quality=")[1]
                qual_str = part.split()[0]
                num, denom = map(int, qual_str.split('/'))
                if denom > 0:
                    percent = int(num * 100 / denom)
                    return percent
    except Exception:
        pass
    return None

def get_active_wifi_interface():
    for iface in pick_interfaces("wifi"):
        if is_up(iface) and get_ssid(iface):
            return iface
    return None

# ----------------------------------------------------------------------
# NEW: Multi‑graph screen (three graphs stacked vertically)
# ----------------------------------------------------------------------
def get_zigbee_interfaces():
    """Return list of network interfaces that look like Zigbee/802.15.4."""
    ifaces = psutil.net_if_addrs().keys()
    zigbee_names = ["wpan", "zboss", "zigbee", "lowpan", "ieee802154"]
    matches = []
    for iface in ifaces:
        if any(pattern in iface.lower() for pattern in zigbee_names):
            matches.append(iface)
    return matches

def draw_small_graph(stdscr, y_start, iface, history, width, max_height):
    """Draw a single small graph at given row, returns next y position."""
    if not history:
        return y_start + max_height + 1

    # Title
    try:
        stdscr.addstr(y_start, 1, f"{iface}:", curses.A_BOLD | curses.color_pair(4))
    except:
        pass
    y_start += 1

    # Plot area
    plot_height = max_height - 2  # leave one line for percentage
    if plot_height < 1:
        plot_height = 1

    # Current quality
    current_q = history[-1]
    try:
        stdscr.addstr(y_start + plot_height, 2, f"{current_q}%", curses.color_pair(1))
    except:
        pass

    # Draw bars
    if len(history) > width:
        plot_data = history[-width:]
    else:
        plot_data = [None] * (width - len(history)) + history

    for col in range(width):
        q = plot_data[col]
        if q is None:
            continue
        bar_height = int(q * plot_height / 100)
        if bar_height == 0 and q > 0:
            bar_height = 1
        for row in range(bar_height):
            y = y_start + plot_height - 1 - row
            x = 2 + col
            if 0 <= y < curses.LINES and 0 <= x < curses.COLS:
                try:
                    stdscr.addch(y, x, '#', curses.color_pair(1))
                except:
                    pass

    # Horizontal axis
    axis_y = y_start + plot_height
    if axis_y < curses.LINES:
        for x in range(2, 2 + width):
            try:
                stdscr.addch(axis_y, x, curses.ACS_HLINE)
            except:
                pass
    return y_start + plot_height + 2  # next start line

def draw_multi_signal_graph(stdscr, duration_seconds):
    """Draw three stacked graphs: wlan0, wlan1, zigbee (if present)."""
    curses.curs_set(0)
    stdscr.nodelay(True)
    setup_colors()

    # Determine which interfaces to show
    wifi_ifaces = pick_interfaces("wifi")
    # Filter only wlan0, wlan1 style (take first two)
    wifi_ifaces = [i for i in wifi_ifaces if i.startswith("wlan")][:2]
    zigbee_ifaces = get_zigbee_interfaces()
    # If no zigbee, maybe show a placeholder or skip
    all_ifaces = wifi_ifaces + zigbee_ifaces[:1]  # max 3 graphs
    if not all_ifaces:
        # Fallback: show a message
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        msg = "No suitable interfaces found (wlan0, wlan1, zigbee)"
        stdscr.addstr(h//2, max(0, (w - len(msg))//2), msg, curses.color_pair(3))
        stdscr.refresh()
        time.sleep(2)
        return

    # History storage for each interface
    max_history = 60
    histories = {iface: [] for iface in all_ifaces}
    start_time = time.time()
    end_time = start_time + duration_seconds

    while time.time() < end_time:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        if h < 15:  # minimal height
            try:
                stdscr.addstr(0, 0, "Screen too short for graphs", curses.color_pair(2))
                stdscr.refresh()
                time.sleep(1)
                return
            except:
                pass

        # Title
        title = "Signal Quality Graphs"
        try:
            stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
            stdscr.addstr(0, max(0, (w - len(title))//2), title)
            stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
        except:
            pass

        # Countdown
        remaining = int(end_time - time.time())
        countdown_msg = f"Dashboard in {remaining}s"
        try:
            stdscr.addstr(0, max(0, w - len(countdown_msg) - 1), countdown_msg, curses.color_pair(3))
        except:
            pass

        # Update histories
        for iface in all_ifaces:
            quality = get_wifi_signal_quality(iface) if "wlan" in iface else None
            if quality is None and "wpan" in iface:  # try same method for zigbee
                quality = get_wifi_signal_quality(iface)  # might work
            if quality is not None:
                histories[iface].append(quality)
                if len(histories[iface]) > max_history:
                    histories[iface].pop(0)

        # Calculate available height per graph
        num_graphs = len(all_ifaces)
        total_height = h - 5  # leave top/bottom margins
        height_per_graph = max(4, total_height // num_graphs)

        # Draw each graph
        y = 2
        for iface in all_ifaces:
            if y >= h - 2:
                break
            graph_width = w - 6
            if graph_width < 5:
                graph_width = 5
            y = draw_small_graph(stdscr, y, iface, histories[iface], graph_width, height_per_graph)

        # Bottom hint
        try:
            stdscr.addstr(h-1, 0, "q=quit", curses.color_pair(5))
        except:
            pass

        stdscr.refresh()

        # One second per update
        for _ in range(4):
            time.sleep(0.25)
            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q')):
                return

# ----------------------------------------------------------------------
# draw_dashboard (with countdown, same as your working version)
# ----------------------------------------------------------------------
def draw_dashboard(stdscr, max_duration=None):
    curses.curs_set(0)
    stdscr.nodelay(True)
    setup_colors()

    start_time = time.time()
    end_time = start_time + max_duration if max_duration else None

    while True:
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

        # Countdown on dashboard
        if max_duration:
            remaining = int(end_time - time.time())
            countdown_msg = f"Graph in {remaining}s"
            try:
                stdscr.addstr(2, max(0, w - len(countdown_msg) - 1), countdown_msg, curses.color_pair(3))
            except curses.error:
                pass

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

        for _ in range(int(REFRESH_SECONDS * 4)):
            if end_time is not None and time.time() >= end_time:
                return
            time.sleep(0.25)
            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q')):
                return

# ----------------------------------------------------------------------
# Main cycling loop (uses multi‑graph instead of single graph)
# ----------------------------------------------------------------------
def run_cycler(stdscr):
    while True:
        draw_dashboard(stdscr, max_duration=DASHBOARD_DURATION)
        draw_multi_signal_graph(stdscr, GRAPH_DURATION)

def main():
    try:
        curses.wrapper(run_cycler)
    except KeyboardInterrupt:
        print("Exiting...")
        sys.exit(0)

if __name__ == '__main__':
    main()