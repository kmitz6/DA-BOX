#!/usr/bin/env python3
import curses, time, socket, struct, subprocess, psutil, sys, glob

REFRESH_SECONDS = 2

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

def name_resolution() -> tuple[str | None, str | None]:
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
                        break               # we only need the first entry
    except Exception:
        pass

    if dns_ip:                               # optional: only try if we have a DNS server
        try:
            infos = socket.getaddrinfo(test_fqdn, None, socket.AF_INET, socket.SOCK_STREAM)
            if infos:
                test_ip = infos[0][4][0]      # first IPv4 address
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
    curses.init_pair(1, curses.COLOR_GREEN,0)
    curses.init_pair(2, curses.COLOR_RED,0)
    curses.init_pair(3, curses.COLOR_YELLOW,0)
    curses.init_pair(4, curses.COLOR_CYAN,0)
    curses.init_pair(5, curses.COLOR_MAGENTA,0)

def draw_dashboard(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    setup_colors()

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        title = "DA BOX by kmitz6"
        subtitle = f"Refresh every {REFRESH_SECONDS}s"
        stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscr.addstr(2, max(0, 1), title)
        stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscr.addstr(3, max(0, 1), subtitle, curses.color_pair(5))

        sections = [ ("Wi‑Fi", "wifi"), ("Ethernet", "eth"), ("Bluetooth", "bt") ]
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
        dns_server_ip, dns_result, fqdn_to_test =  name_resolution()

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
        tests = [("df gateway", gw), ("quad9 dns", "9.9.9.9"), ("allegro.pl", "allegro.pl"), ("facebook.com", "facebook.com")]

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
        for _ in range(int(REFRESH_SECONDS*4)):
            time.sleep(0.25)
            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q')):
                return

def main():
    try:
        curses.wrapper(draw_dashboard)
    except KeyboardInterrupt:
        print("Exiting...")
        sys.exit(0)

if __name__ == '__main__':
    main()
