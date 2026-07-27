# How to use the DNS model

## Build a query

<!-- name: test_use_dns -->
```python
from rustruct.protocols.dns import DNS, DNSFlags, Question

query = DNS(
    id=0x1234,
    flags=DNSFlags(rd=True),
    questions=[Question("example.com")],
)
wire = query.pack()
```

## Decode the response or query

<!-- name: test_use_dns -->
```python
decoded = DNS.unpack(wire)
assert decoded.questions[0].name == "example.com"
```

The model supports all four DNS sections and A, AAAA, NS, CNAME, PTR, MX, SOA,
and TXT records. Unknown RDATA remains available as bytes and can be repacked.

DNS name compression depends on absolute message offsets, so the module uses a
hand-written outer parser around declarative fixed components. See
{doc}`/explanation/extension-model` for why this boundary exists.

See {doc}`/reference/protocols` for the DNS classes and enums.
