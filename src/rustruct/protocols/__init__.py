"""Real-world wire formats, as a worked example of the declarative Struct
frontend."""

from rustruct.protocols.inet import IPPayload, IPProtocol, IPv4, IPv6Address
from rustruct.protocols.tcp import TCP
from rustruct.protocols.udp import UDP

__all__ = ["IPPayload", "IPProtocol", "IPv4", "IPv6Address", "TCP", "UDP"]
