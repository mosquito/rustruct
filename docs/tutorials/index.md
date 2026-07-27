# Tutorials

Tutorials are guided learning experiences, split into short, numbered pages
you follow in order -- each links to the next. The first five build one real
thing: the frame format for a tiny chat relay, the kind of protocol a small
TCP server might speak. The second five apply the same workflow to formats
someone else already designed.

## Build a chat relay's frame format

A chat relay forwards short messages between connected clients over TCP.
Every message on the wire needs a type tag, a sequence number so drops are
detectable, and protection against a corrupted read being mistaken for a
valid message. By the end of this sequence you will have defined that frame,
packed and decoded it, read it from a socket that delivers bytes in
arbitrary chunks, and handled a corrupted frame.

1. {doc}`install-rustruct`
2. {doc}`define-and-pack-a-structure`
3. {doc}`decode-the-message`
4. {doc}`try-partial-input`
5. {doc}`inspect-an-error`

## Use the bundled protocol and file formats

1. {doc}`decode-an-ipv4-packet`
2. {doc}`build-an-ipv4-packet-with-udp`
3. {doc}`build-and-decode-a-dns-query`
4. {doc}`create-a-minimal-png-container`
5. {doc}`create-a-transparent-png`

```{toctree}
:hidden:
:maxdepth: 1

install-rustruct
define-and-pack-a-structure
decode-the-message
try-partial-input
inspect-an-error
decode-an-ipv4-packet
build-an-ipv4-packet-with-udp
build-and-decode-a-dns-query
create-a-minimal-png-container
create-a-transparent-png
```
