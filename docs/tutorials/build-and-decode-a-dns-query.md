# 3. Build and decode a DNS query

DNS is a pointer-heavy format: names inside a message can reference bytes
earlier in the *same* message instead of repeating them, so decoding one
needs state no single field-by-field schema can express on its own -- the
kind of case {doc}`/how-to/extend-schema` covers. The bundled model handles
that internally; from the caller's side, DNS still looks like an ordinary
typed message with flags, questions, and resource records:

<!-- name: test_tutorial_dns_query -->
```python
from rustruct.protocols.dns import DNS, DNSFlags, Question

query = DNS(
    id=0x1234,
    flags=DNSFlags(rd=True),
    questions=[Question("example.com")],
)
wire = query.pack()
decoded = DNS.unpack(wire)

assert decoded.id == 0x1234
assert decoded.flags.rd == 1
assert decoded.questions[0].name == "example.com"
assert decoded.pack() == wire
```

The same API handles compressed names when decoding existing messages.
Unsupported record data remains available as raw bytes so it can be repacked.

Previous: {doc}`build-an-ipv4-packet-with-udp`. Next: {doc}`create-a-minimal-png-container`.
