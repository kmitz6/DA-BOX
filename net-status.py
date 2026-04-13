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
# Helper functions (unchanged from original)
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
# Wi-Fi signal quality
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
# Dashboard (with countdown and compact layout for small screen)
# ----------------------------------------------------------------------
def draw_dashboard(stdscr, max_duration=None):
    curses.curs_set(0)
    stdscr.nodelay(True)
    setup_colors()

    start_time = time.time()
    end_time = start_time + max_duration if max_duration else None

    while True:
        if end_time and time.time() >= end_time:
            return

        stdscr.erase()
        h, w = stdscr.getmaxyx()
        if h < 8 or w < 30:
            # Screen too small – show message and wait
            try:
                msg = f"Screen too small: {w}x{h}"
                stdscr.addstr(0, 0, msg, curses.color_pair(3))
                stdscr.refresh()
                time.sleep(2)
            except:
                pass
            return

        # Title and subtitle
        title = "DA BOX by kmitz6"
        subtitle = f"Refresh {REFRESH_SECONDS}s"
        try:
            stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
            stdscr.addstr(0, 0, title[:w-1])
            stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
            stdscr.addstr(1, 0, subtitle, curses.color_pair(5))
        except:
            pass

        # Countdown (top right)
        if max_duration:
            remaining = int(end_time - time.time())
            countdown_msg = f"Graph in {remaining}s"
            try:
                stdscr.addstr(0, max(0, w - len(countdown_msg) - 1), countdown_msg, curses.color_pair(3))
            except:
                pass

        y = 3
        sections = [("WiFi", "wifi"), ("Eth", "eth"), ("BT", "bt")]
        if h > y + 6:
            try:
                stdscr.addstr(y, 0, "Networks:", curses.A_BOLD)
                y += 1
            except:
                pass
            for label, kind in sections:
                if y >= h-3:
                    break
                ifaces = pick_interfaces(kind)
                try:
                    stdscr.addstr(y, 1, f"{label}:", curses.A_BOLD)
                    y += 1
                except:
                    y += 1
                    continue
                if not ifaces:
                    try:
                        stdscr.addstr(y, 2, "none")
                        y += 1
                    except:
                        y += 1
                    continue
                for iface in ifaces:
                    if y >= h-3:
                        break
                    up = is_up(iface)
                    ip = get_ip(iface) or '-'
                    color = curses.color_pair(1) if up else curses.color_pair(2)
                    try:
                        stdscr.addstr(y, 2, f"{iface} ")
                        stdscr.addstr("UP" if up else "DN", color | curses.A_BOLD)
                        y += 1
                        stdscr.addstr(y, 3, f"IP:{ip[:12]}")
                        y += 1
                    except:
                        y += 2
                        break

        # Gateway and ping
        if h > y + 4:
            try:
                y += 1
                stdscr.addstr(y, 0, "Gateway/Ping:", curses.A_BOLD)
                y += 1
                gw, _ = get_default_gateway()
                if gw:
                    stdscr.addstr(y, 1, f"GW:{gw[:15]}")
                else:
                    stdscr.addstr(y, 1, "GW:none")
                y += 1
                ok = ping_ok("8.8.8.8")
                result = "OK" if ok else "FAIL"
                color = curses.color_pair(1) if ok else curses.color_pair(2)
                stdscr.addstr(y, 1, f"Ping 8.8.8.8: {result}", color)
                y += 1
            except:
                pass

        # USB devices
        if h > y + 3:
            try:
                y += 1
                stdscr.addstr(y, 0, "USB:", curses.A_BOLD)
                y += 1
                usb = list_usb_devices()
                if usb:
                    stdscr.addstr(y, 1, usb[0][:w-3])
                else:
                    stdscr.addstr(y, 1, "none")
            except:
                pass

        # Quit hint
        try:
            stdscr.addstr(h-1, 0, "q=quit", curses.color_pair(5))
        except:
            pass

        stdscr.refresh()

        # Sleep in small chunks
        for _ in range(int(REFRESH_SECONDS * 4)):
            if end_time and time.time() >= end_time:
                return
            time.sleep(0.25)
            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q')):
                return

# ----------------------------------------------------------------------
# Signal graph (with countdown and compact drawing)
# ----------------------------------------------------------------------
def draw_signal_graph(stdscr, duration_seconds):
    curses.curs_set(0)
    stdscr.nodelay(True)
    setup_colors()

    iface = get_active_wifi_interface()
    if not iface:
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        msg = "No active Wi-Fi"
        try:
            stdscr.addstr(h//2, max(0, (w - len(msg))//2), msg, curses.color_pair(3))
            stdscr.addstr(h//2+2, max(0, (w-20)//2), "Press any key")
            stdscr.refresh()
            stdscr.nodelay(False)
            stdscr.getch()
        except:
            pass
        return

    max_history = 60
    history = []
    start_time = time.time()
    end_time = start_time + duration_seconds

    while time.time() < end_time:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        # Title and SSID
        title = f"Wi-Fi Signal - {iface}"
        ssid = get_ssid(iface) or "unknown"
        try:
            stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
            stdscr.addstr(0, max(0, (w - len(title))//2), title[:w-1])
            stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
            stdscr.addstr(1, 0, f"SSID: {ssid[:w-6]}", curses.color_pair(5))
        except:
            pass

        # Countdown to dashboard (top right)
        remaining = int(end_time - time.time())
        countdown_msg = f"Dashboard in {remaining}s"
        try:
            stdscr.addstr(0, max(0, w - len(countdown_msg) - 1), countdown_msg, curses.color_pair(3))
        except:
            pass

        # Get quality
        quality = get_wifi_signal_quality(iface)
        if quality is not None:
            history.append(quality)
            if len(history) > max_history:
                history.pop(0)

        # Draw graph
        if history:
            graph_height = min(h - 5, 8)   # compact height for small screen
            graph_width = w - 4
            if len(history) > graph_width:
                plot_data = history[-graph_width:]
            else:
                plot_data = [None] * (graph_width - len(history)) + history

            for col in range(graph_width):
                q = plot_data[col]
                if q is None:
                    continue
                bar_height = max(1, int(q * graph_height / 100))
                for row in range(bar_height):
                    y = h - 4 - row
                    x = 2 + col
                    if 0 <= y < h and 0 <= x < w:
                        try:
                            stdscr.addch(y, x, '#', curses.color_pair(1))
                        except:
                            pass

            # Horizontal axis
            axis_y = h - 4 - graph_height
            if axis_y >= 0:
                for x in range(2, 2 + graph_width):
                    try:
                        stdscr.addch(axis_y, x, curses.ACS_HLINE)
                    except:
                        pass
                for y in range(axis_y, h - 3):
                    try:
                        stdscr.addch(y, 1, curses.ACS_VLINE)
                    except:
                        pass
                try:
                    stdscr.addch(axis_y, 1, curses.ACS_LTEE)
                except:
                    pass

            # Labels
            if graph_height >= 2:
                try:
                    stdscr.addstr(axis_y, 0, "100%", curses.A_BOLD)
                    mid_y = axis_y + graph_height // 2
                    stdscr.addstr(mid_y, 0, "50%", curses.A_BOLD)
                    bot_y = h - 4
                    stdscr.addstr(bot_y, 0, "0%", curses.A_BOLD)
                except:
                    pass

            current_q = history[-1] if history else 0
            try:
                stdscr.addstr(h - 2, 0, f"Signal: {current_q}%", curses.color_pair(4))
            except:
                pass
        else:
            try:
                stdscr.addstr(h//2, max(0, (w-20)//2), "Waiting for data...", curses.color_pair(3))
            except:
                pass

        stdscr.refresh()

        # One second per sample, responsive to quit
        for _ in range(4):
            time.sleep(0.25)
            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q')):
                return

# ----------------------------------------------------------------------
# Main cycler
# ----------------------------------------------------------------------
def run_cycler(stdscr):
    while True:
        draw_dashboard(stdscr, max_duration=DASHBOARD_DURATION)
        draw_signal_graph(stdscr, GRAPH_DURATION)

def main():
    try:
        curses.wrapper(run_cycler)
    except KeyboardInterrupt:
        print("Exiting...")
        sys.exit(0)

if __name__ == '__main__':
    main()