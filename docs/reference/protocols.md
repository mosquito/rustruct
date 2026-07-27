# Protocol and format API

Worked examples are available for {doc}`/how-to/use-ipv4`,
{doc}`/how-to/use-dns`, {doc}`/how-to/use-png`,
and {doc}`/how-to/use-msgpack`.

## IP, TCP, and UDP

```{eval-rst}
.. autoclass:: rustruct.protocols.IPv4
   :members: build, pack, unpack

.. autoclass:: rustruct.protocols.IPProtocol
   :members:

.. autoclass:: rustruct.protocols.IPPayload

.. autoclass:: rustruct.protocols.TCP

.. autoclass:: rustruct.protocols.UDP
```

## DNS

```{eval-rst}
.. automodule:: rustruct.protocols.dns

.. autoclass:: rustruct.protocols.dns.DNS
   :members: pack, unpack

.. autoclass:: rustruct.protocols.dns.DNSFlags
.. autoclass:: rustruct.protocols.dns.Question
.. autoclass:: rustruct.protocols.dns.ResourceRecord
.. autoclass:: rustruct.protocols.dns.RRType
.. autoclass:: rustruct.protocols.dns.DNSClass
.. autoclass:: rustruct.protocols.dns.A
.. autoclass:: rustruct.protocols.dns.AAAA
.. autoclass:: rustruct.protocols.dns.NS
.. autoclass:: rustruct.protocols.dns.CNAME
.. autoclass:: rustruct.protocols.dns.PTR
.. autoclass:: rustruct.protocols.dns.MX
.. autoclass:: rustruct.protocols.dns.SOA
.. autoclass:: rustruct.protocols.dns.TXT
.. autoclass:: rustruct.protocols.dns.UnknownRData
```

## PNG

```{eval-rst}
.. autoclass:: rustruct.formats.image.png.PNG
.. autoclass:: rustruct.formats.image.png.Chunk
.. autoclass:: rustruct.formats.image.png.IHDR
.. autoclass:: rustruct.formats.image.png.ChunkType
.. autoclass:: rustruct.formats.image.png.ColorType
```

## MessagePack

```{eval-rst}
.. automodule:: rustruct.formats.msgpack

.. autoclass:: rustruct.formats.msgpack.Value

.. autofunction:: rustruct.formats.msgpack.decode
.. autofunction:: rustruct.formats.msgpack.decode_from
.. autofunction:: rustruct.formats.msgpack.encode
```
