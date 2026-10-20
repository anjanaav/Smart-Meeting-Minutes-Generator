"""Jitsi meeting-link validation. The bot's browser will open whatever host is accepted here, so private
and loopback addresses are rejected to avoid turning the bot into a network probe (SSRF)."""
from __future__ import annotations

import ipaddress
import re
import secrets
import socket
from urllib.parse import unquote, urlparse

from .errors import ApiError

_ROOM = re.compile(r"^[\w.\-~]{1,200}$", re.UNICODE)
_BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp")


def default_resolver(host: str) -> list:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _bad(code: str, message: str) -> ApiError:
    return ApiError(400, code, message)


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def _check_host(host: str, allowed: tuple, resolver) -> None:
    h = host.lower().rstrip(".")
    if allowed and h not in {a.lower() for a in allowed}:
        raise _bad("HOST_NOT_ALLOWED", "This Jitsi server is not allowed.")
    if h == "localhost" or h.endswith(_BLOCKED_SUFFIXES):
        raise _bad("INVALID_MEETING_LINK", "The meeting link must point to a public Jitsi server.")
    try:
        ips = [h]
        ipaddress.ip_address(h)
    except ValueError:
        try:
            ips = resolver(h)
        except OSError:
            raise _bad("HOST_UNRESOLVABLE", "The Jitsi server in this link could not be found.") from None
    if not ips or not all(_is_public(i) for i in ips):
        raise _bad("INVALID_MEETING_LINK", "The meeting link must point to a public Jitsi server.")


def _room(path_parts: list) -> str:
    parts = [unquote(p) for p in path_parts if p]
    if not 1 <= len(parts) <= 2 or not all(_ROOM.match(p) and p.strip(".") for p in parts):
        raise _bad("INVALID_MEETING_LINK", "The meeting link does not contain a valid room name.")
    return "/".join(parts)


def parse_meeting(meeting_url: str, room_name: str, default_server: str, allowed_hosts: tuple = (), resolver=default_resolver) -> dict:
    """Return {"server", "room", "url", "room_key"}. A meeting link wins over a room name; with neither, a room is generated."""
    meeting_url, room_name = (meeting_url or "").strip(), (room_name or "").strip()
    if meeting_url:
        if "//" not in meeting_url:
            meeting_url = "https://" + meeting_url
        u = urlparse(meeting_url)
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise _bad("INVALID_MEETING_LINK", "Enter a valid https Jitsi meeting link.")
        try:
            port = u.port
        except ValueError:
            raise _bad("INVALID_MEETING_LINK", "Enter a valid https Jitsi meeting link.") from None
        host = u.hostname
        room = _room(u.path.split("/"))
        server = f"https://{host}" + (f":{port}" if port and port != 443 else "")
    else:
        d = urlparse(default_server)
        host, server = d.hostname, f"{d.scheme}://{d.netloc}"
        room = _room([room_name]) if room_name else "smmg-" + secrets.token_hex(6)
    _check_host(host, allowed_hosts, resolver)
    return {"server": server, "room": room, "url": f"{server}/{room}", "room_key": f"{server.split('://')[1].lower()}/{room.lower()}"}
