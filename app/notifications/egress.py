"""Egress controls for outbound notification webhooks.

A webhook destination is operator-configured, so it is trusted as a
*configuration* but never as a *network peer*: it may still name a link-local
metadata address (169.254.169.254), an internal service, or a public name
that resolves into private space. Every destination is therefore re-validated
immediately before a request is made:

* **Scheme.** HTTPS is required. Plain HTTP is accepted only for an
  explicitly enabled loopback development target
  (``ADVISORY_NOTIFICATION_ALLOW_INSECURE_LOOPBACK=1``).
* **Address.** The hostname is resolved and *every* returned address is
  classified. A single private, loopback, link-local, multicast, shared or
  reserved address refuses the whole target, so a partially private
  resolution cannot slip through.
* **Pinning.** The request is made against a validated address with a pinned
  socket, so a second DNS answer cannot move the connection to another host.
  TLS is still validated against the requested hostname.
* **Redirects.** Redirects are followed only inside the original origin and
  only after the new target passes the same checks. A redirect to another
  host is refused, never followed.
* **Bounds.** The connect and read timeouts are bounded and the response body
  is size-capped, so a slow or enormous peer cannot pin a worker.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse

#: Environment variable that permits a plain-HTTP loopback development
#: target. It never permits a non-loopback plain-HTTP target.
ALLOW_LOOPBACK_ENV = "ADVISORY_NOTIFICATION_ALLOW_INSECURE_LOOPBACK"

#: Bounded connect/read timeouts (seconds).
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 10.0

#: Maximum accepted response body size (bytes).
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024

#: Maximum number of redirects that may be followed inside one origin.
MAX_REDIRECTS = 3

#: Address classes that are never reachable from a notification webhook.
_BLOCKED_CLASSES = frozenset({"private", "link_local", "multicast", "reserved", "unspecified"})

#: Extra non-public ranges that are not covered by the stdlib predicates.
_EXTRA_BLOCKED_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),  # carrier-grade NAT
    ipaddress.ip_network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
)


class TargetError(Exception):
    """A destination was refused. ``reason`` is a closed-vocabulary code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Target:
    """A validated destination."""

    scheme: str
    host: str
    port: int
    path: str
    addresses: tuple[str, ...]

    @property
    def origin(self) -> tuple[str, str, int]:
        """The origin triple used for the same-origin redirect rule."""
        return (self.scheme, self.host.lower(), self.port)

    @property
    def display_host(self) -> str:
        """Host only - safe to show to an administrator."""
        return self.host

    @property
    def url(self) -> str:
        """The canonical URL of this target."""
        return urlunparse((self.scheme, self.host, self.path, "", "", ""))


def loopback_allowed() -> bool:
    """True when the operator explicitly enabled a loopback dev target."""
    import os

    return os.getenv(ALLOW_LOOPBACK_ENV, "") == "1"


def classify_address(raw: str) -> str:
    """Classify one resolved address: ``public``, ``loopback`` or a block."""
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return "reserved"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link_local"
    if address.is_multicast:
        return "multicast"
    if address.is_unspecified:
        return "unspecified"
    if address.is_private:
        return "private"
    if any(address in network for network in _EXTRA_BLOCKED_NETWORKS):
        return "reserved"
    if address.is_reserved:
        return "reserved"
    return "public"


def _resolve(host: str, port: int) -> tuple[str, ...]:
    """Resolve a host to the set of addresses a connection could use."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise TargetError("dns_failed", type(exc).__name__) from None
    addresses: list[str] = []
    for info in infos:
        raw = info[4][0]
        if raw not in addresses:
            addresses.append(raw)
    if not addresses:
        raise TargetError("dns_failed")
    return tuple(addresses)


def validate_target(url: str, *, allow_loopback: bool | None = None) -> Target:
    """Validate a webhook destination and return it ready for a request.

    Raises :class:`TargetError` with a closed-vocabulary reason code when the
    destination must not be contacted.
    """
    if allow_loopback is None:
        allow_loopback = loopback_allowed()
    raw = (url or "").strip()
    if not raw or len(raw) > 500:
        raise TargetError("target_invalid")
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise TargetError("scheme_refused")
    if parsed.username or parsed.password:
        # Credentials in a URL would end up in a log line or a redirect chain.
        raise TargetError("credentials_in_url")
    host = parsed.hostname
    if not host:
        raise TargetError("target_invalid")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise TargetError("target_invalid") from None
    if not 1 <= port <= 65535:
        raise TargetError("target_invalid")

    addresses = _resolve(host, port)
    loopback = False
    for raw_address in addresses:
        kind = classify_address(raw_address)
        if kind == "loopback":
            loopback = True
            continue
        if kind in _BLOCKED_CLASSES:
            raise TargetError("blocked_address", kind)
    if loopback:
        if not allow_loopback:
            raise TargetError("blocked_address", "loopback")
        if scheme != "http":
            # A loopback development target is plain HTTP by definition; TLS
            # to loopback would need a trusted certificate for it.
            raise TargetError("scheme_refused")
    elif scheme != "https":
        raise TargetError("scheme_refused")

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return Target(scheme=scheme, host=host, port=port, path=path, addresses=addresses)


def _open_socket(target: Target, connect_timeout: float) -> socket.socket:
    """Connect to a validated address, pinning the socket to that address."""
    last_error: Exception | None = None
    for raw_address in target.addresses:
        try:
            sock = socket.create_connection((raw_address, target.port), timeout=connect_timeout)
        except OSError as exc:
            last_error = exc
            continue
        sock.settimeout(connect_timeout)
        return sock
    raise TargetError("connection_failed", type(last_error).__name__ if last_error else "")


def _host_header(target: Target) -> str:
    """The Host header of the request.

    The default port of the scheme is left out, so an HTTPS request on 443
    looks exactly like one made by any other client.
    """
    default_port = 443 if target.scheme == "https" else 80
    if target.port == default_port:
        return target.host
    return f"{target.host}:{target.port}"


def _perform(
    target: Target,
    body: bytes,
    headers: dict[str, str],
    *,
    connect_timeout: float,
    read_timeout: float,
    max_response_bytes: int,
) -> tuple[int, dict[str, str]]:
    """POST ``body`` to ``target`` and return the status and response headers."""
    sock = _open_socket(target, connect_timeout)
    connection: http.client.HTTPConnection | None = None
    request_headers = dict(headers)
    request_headers.setdefault("Host", _host_header(target))
    try:
        if target.scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=target.host)
        connection = http.client.HTTPConnection(target.host, target.port, timeout=read_timeout)
        # The socket is already connected to a validated address; the Host
        # header still names the requested hostname so TLS and HTTP agree.
        connection.sock = sock
        connection.request("POST", target.path, body=body, headers=request_headers)
        response = connection.getresponse()
        received = response.read(max_response_bytes + 1)
        if len(received) > max_response_bytes:
            raise TargetError("response_too_large")
        return response.status, {k.lower(): v for k, v in response.getheaders()}
    except TargetError:
        raise
    except ssl.SSLError as exc:
        raise TargetError("tls_error", type(exc).__name__) from None
    except TimeoutError:
        raise TargetError("timeout") from None
    except UnicodeEncodeError:
        raise TargetError("target_invalid") from None
    except (http.client.HTTPException, OSError, ValueError) as exc:
        raise TargetError("connection_failed", type(exc).__name__) from None
    finally:
        if connection is not None:
            connection.close()
        else:
            sock.close()


def post_json(
    url: str,
    body: bytes,
    headers: dict[str, str],
    *,
    allow_loopback: bool | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> int:
    """POST a JSON body to ``url`` and return the final HTTP status.

    The destination is validated again here, so a target that was safe when
    it was configured is refused if it has become unsafe since.
    """
    target = validate_target(url, allow_loopback=allow_loopback)
    current = target
    for hop in range(MAX_REDIRECTS + 1):
        status, response_headers = _perform(
            current,
            body,
            headers,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_response_bytes=max_response_bytes,
        )
        if status not in {301, 302, 303, 307, 308}:
            return status
        location = response_headers.get("location")
        if not location:
            return status
        if hop >= MAX_REDIRECTS:
            raise TargetError("too_many_redirects")
        next_url = urljoin(current.url, location)
        next_target = validate_target(next_url, allow_loopback=allow_loopback)
        if next_target.origin != current.origin:
            # A redirect to another host is the classic SSRF pivot: refuse it.
            raise TargetError("redirect_refused")
        current = next_target
    raise TargetError("too_many_redirects")
