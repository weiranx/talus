# Garmin BLE FileSync — notes from the Mac client

Reverse-engineering notes for `sync.swift`, captured against a **fēnix 9 Pro 51 mm**
(product 4956, firmware 6.48, GFDI protocol 151) on macOS on 2026-10-11. Gadgetbridge's
`CommunicatorV2`, `MlrCommunicator` and `FileTransferHandler` were the starting point.
Everything here is read-only: nothing is marked synced, archived or deleted on the watch.

All multi-byte integers are **little-endian** unless stated otherwise.

## Files

| File | What |
| --- | --- |
| `scan.swift` | Passive BLE scan; never connects. |
| `sync.swift` | The client: connect, ML, GFDI handshake, FileSync listing, MLR file download, inflate. |
| `build.sh` | Builds both into tiny `.app` bundles (`swiftc -O`, ad-hoc signed) so macOS attributes Bluetooth permission to them. |
| `run.sh` | `./run.sh scan [secs]` / `./run.sh sync --name 4956 --download 2` — runs via `open -W` and tees stdout to a log. |
| `Info.plist` | Template with `NSBluetoothAlwaysUsageDescription`. |
| `filesync-listing.txt` | Real FileSync listing from the watch (192 files): `kind  id1-id2  code nameId size page timestamp`. |

`sync.swift` flags: `--name` (substring of GAP name or DIS model, e.g. `4956`), `--identify`
(stop before touching ML), `--list` (default), `--download N` (newest N per wanted kind),
`--out DIR`, `--timeout SEC`, `--gb-flags` (send Gadgetbridge's list filter), `--plain-transfer`
(register 0x2018 without MLR — does not work, see quirks).

## 1. Discovery

- The watch advertises **with no local name and no service UUIDs**. Identify it by manufacturer
  data: company id `0x0087` (Garmin, bytes `87 00`) followed by the **product id big-endian**
  (`87 00 13 5c` → 0x135C = 4956).
- GATT services seen: `6A4E2800-…` (ML), `6A4E8022-…`, `3802`, `CC353442-BE58-4EA2-876E-11D8D6976366`,
  Battery, Device Information (`180A`), Heart Rate, RSC.
- DIS `2A29` = `Garmin`, `2A24` (model number) = `4956`. The client reads DIS *before* enabling ML
  notifications so it can bail out on the wrong watch without triggering pairing.
- Web Bluetooth: `requestDevice({ filters: [{ manufacturerData: [{ companyIdentifier: 0x0087 }] }],
  optionalServices: ['6a4e2800-667b-11e3-949a-0800200c9a66', 'device_information'] })`.

## 2. ML (multi-link) transport

UUID template `6A4E%04X-667B-11E3-949A-0800200C9A66`.

- Service `0x2800`. Characteristic pairs: receive (notify) `0x2810..0x2814`, send `0x2820..0x2824`
  (receive + 0x10). Also present: `0x2803`, `0x2830/0x2840`. We use the first pair, `2810` / `2820`.
- `2810` props `0x1c` (write-without-response, write, notify), `2820` props `0x0c` (write + write-without-response).
  We write **with response** when allowed and keep one write in flight at a time.
- macOS max write (without response) = **229** bytes; GFDI chunks are `maxWrite - 1` (one byte for the handle).
- Every ML packet, both directions: `[handle u8][payload…]`. Handle `0` is management.

### Management messages (handle 0)

`[0x00][type u8][clientId u64][…]`, clientId = `2` (Gadgetbridge's; any constant works).

| type | name | rest of the packet |
| --- | --- | --- |
| 0 | REGISTER_REQ | `[service u16][reliable u8]` |
| 1 | REGISTER_RESP | `[service u16][status u8 (0 = ok)][handle u8][reliable u8]` |
| 2 | CLOSE_REQ | `[service u16][handle u8]` (not sent by us) |
| 3 | CLOSE_RESP | `[service u16][handle u8]` — the watch sends this **unsolicited** when a file transfer ends |
| 5 | CLOSE_ALL_REQ | `[0x0000 u16]` |
| 6 | CLOSE_ALL_RESP | |

Exact bytes we send:

```
close all:      00 05 02 00 00 00 00 00 00 00 00 00
register GFDI:  00 00 02 00 00 00 00 00 00 00 01 00 00        (service 0x0001, reliable 0)
register file:  00 00 02 00 00 00 00 00 00 00 18 20 02        (service 0x2018, reliable 2 = MLR)
```

Sequence: enable notify on `2810` → CLOSE_ALL → REGISTER service 1 → the response carries the
GFDI handle. **Handles are not stable** (seen 1, 2, 3, 8, 11 across reconnects); always use the one
from REGISTER_RESP. The 0x2018 register response came back with handles 129+ and `reliable=1`.

## 3. GFDI over the GFDI handle

- Stream is **COBS-encoded**; each frame is `00 <cobs bytes> 00` (leading *and* trailing zero), split
  across ML packets of `maxWrite-1` bytes, each prefixed with the handle byte. Decoder: buffer
  everything after the handle byte, cut on zero delimiters. See `cobsEncode` / `CobsDecoder`.
- Decoded frame: `[length u16][type u16][payload][crc u16]`, `length` = whole frame **including** the
  CRC. CRC is Garmin's nibble-table CRC-16 (table in `crc16`, init 0) over everything before the CRC.
- Compact type: if `type & 0x8000`, real type = `(type & 0xFF) + 5000`.
- Generic RESPONSE (5000): `[origType u16][status u8 (0 ACK)][extra…]`. ACK every watch message we
  don't otherwise handle with `RESPONSE(type, 0)`.

Message types used: 5000 response, 5002 download request, 5004 file transfer data, 5024 device
information, 5043 protobuf request, 5044 protobuf response, 5050 configuration, 5052 current time
request, 5101 auth negotiation.

### Handshake

1. Right after GFDI is registered the watch sends **DeviceInformation (5024)**:
   `[protocol u16][product u16][unit u32][sw u16 (648 = 6.48)][maxPacket u16 (400)][btName str][name str][model str]`
   (str = `u8 length` + UTF-8). Seen: `bt="fenix 9 Pro - inReach, 5" name="fenix" model="9 Pro - 51mm"`.
2. We reply `RESPONSE(5024, 0)` + `[150 u16][0xFFFF u16][0xFFFFFFFF u32][7791 u16][0xFFFF u16][hostName str]["Apple" str]["Mac" str][protocolFlags u8 = (protocol/100 == 1 ? 1 : 0)]`.
3. Watch sends **Configuration (5050)**: `[n u8][n bytes capability bitmap]` (18 bytes:
   `fbff3f22fede5e4efe7db7df3f0229000100`). We ACK, then send our own 5050 with the same length,
   byte 0 = `0x39` (CONNECT_MOBILE_FIT_LINK | SYNC | DEVICE_INITIATES_SYNC | HOST_INITIATED_SYNC_REQUESTS),
   the rest zero. The watch ACKs.
4. Wait ~1 s, then start file work.
- **AuthNegotiation (5101)** never arrived; the client stops if it does. **CurrentTimeRequest (5052)**
  is answered `RESPONSE(5052, status 2 = UNSUPPORTED)` so the watch clock is never touched.

### Legacy directory (not useful on this watch)

DownloadRequest 5002 `[fileIndex u16 = 0][offset u32 = 0][type u8 = 1 NEW][crcSeed u16 = 0][size u32 = 0][u8 0]`
→ `RESPONSE(5002, 0, [downloadStatus u8][size u32])`, data in 5004 frames
`[flags u8][crc u16 (running CRC seeded with previous chunk's)][offset u32][bytes]`, each ACKed with
`RESPONSE(5004, 0, [status u8 0][nextOffset u32])`. Directory = 16-byte entries
`[index u16][dataType u8][subType u8][fileNumber u16][specificFlags u8][fileFlags u8][size u32][garminTs u32]`.
The fēnix 9 returns **only 2 empty entries (32 B), no FIT files** → it only serves files through
protobuf FileSync. A new client can skip this and go straight to FileSync.

## 4. Protobuf transport (5043 / 5044)

Payload of both: `[requestId u16][dataOffset u32][totalLength u32][chunkLength u32][protobuf bytes]`.

- We send requests as 5043 with an incrementing requestId, offset 0, total = chunk = length.
- The watch immediately answers `RESPONSE(5043, 0, [requestId u16][…])` (seen extra `0100000000000000`),
  then the real answer arrives later as **5044** (or as 5043 when the watch initiates), often in
  several chunks (a 100-entry list page arrives chunked).
- For each non-final chunk reply `RESPONSE(type, 0, [requestId u16][dataOffset u32][chunkStatus u8 0 = KEPT][statusCode u8 0])`;
  reassemble by offset until `totalLength`; for the final chunk a plain `RESPONSE(type, 0)`.
- Payload is a GDI `Smart` message; FileSync is **field 43**.

### FileSync service (Smart field 43) — field numbers we used

```
FileSyncService {
  1  FileRequest           (we send)
  2  FileResponse          (watch)
  5  TransferStatusRequest (watch, after a transfer closes)
  9  FileListRequest       (we send)
  10 FileListResponse      (watch)
  21 TransferStatusResponse(we send)
}

FileListRequest  { 1 cursor uint  | 2 startPage uint ; 4,5 filter FileId (only with --gb-flags) ; 7 uint = 0 }
FileListResponse { 1 status ; 2 cursor (present => more pages) ; 3 nextPage ; 4 repeated File }
File             { 1 FileId { 1 id1 u64 ; 2 id2 u64 } ; 2 FileType { 1 code ; 2 name string ; 3 nameId } ;
                   3 size (UNCOMPRESSED bytes) ; 5 pageId ; 7 timestamp (Garmin epoch seconds) }
FileRequest      { 1 File (the File bytes exactly as received, echoed back) ; 2 = 24 ; 3 = 0 ; 4 = 0 ; 5 = 15 }
FileResponse     { 1 status (0 ok) ; 2 = 0 ; 3 handle ; 7 = 11 }
TransferStatusResponse { 1 = 1 }
```

- Listing: first request without cursor; the watch returns 100 files and `cursor=1`; request again
  with `cursor=1`; the second page had 92 files, no cursor, `nextPage=8663` → done. ~1.5 s per page.
- `FileType.name` is sent only once per name; later entries carry just `nameId`, so keep a
  `nameId → name` map while parsing. nameIds are **not stable across pages** (FIT_TYPE_4 shows nameId 1
  on page 1 and 0 on page 2), so treat them as a per-page string table and update the map whenever a
  name appears. Names are `FIT_TYPE_<FIT file type>` (4 activity, 3 sports, 32 monitor, 49 sleep,
  68 HRV status, 44 metrics…), plus `BACKUP_PRIMARY`, `LiveActivity`, and one nameless `code=3` file.
- `code` was 0 for every FIT file. `id1` looks like `[garmin timestamp u32][counter u32]`.
- FileRequest constants 24/0/0/15 are copied from Gadgetbridge; meaning unknown, they work.
- Example FileResponse bytes: `08 00 10 00 18 b3 09 38 0b` = status 0, f2 0, handle 1203, f7 11.
  File handles increment per request across connections (1201, 1202, … 1206).
- TransferStatusRequest arrives as a watch-initiated **5043**; answer with **5044** using the
  *watch's* requestId and `Smart{43: {21: {1: 1}}}`.

## 5. File bytes: service 0x2018 over MLR

File content does **not** come over GFDI. After a FileResponse with handle `H`:

1. Register ML service `0x2018` with `reliable = 2` → REGISTER_RESP gives a transfer handle `T`
   (129, 130, … — high bit set) and `reliable = 1`.
2. On `T`, send the file request body `00 00 <H u16> 00 00` wrapped in an MLR packet (seq 0).
   Resend after 1 s, 2 s, … (max 5 resends) until a watch packet carries a non-zero reqNum (= acked).
3. Watch streams MLR data packets. The first payload is a 3-byte start header `00 00 00` — drop it.
   Every following payload byte is appended to the compressed buffer.
4. End of file: the watch sends management **CLOSE_RESP (type 3) for service 0x2018**. That's the
   only end signal; then it sends a TransferStatusRequest (see above). Move to the next file.

### MLR header (2 bytes, then payload)

```
byte0 = 1 | handle(3 bits) | reqNum high 4 bits      = 0x80 | (T & 7) << 4 | (reqNum >> 2) & 0x0F
byte1 = reqNum low 2 bits | seq (6 bits)             = (reqNum & 3) << 6 | (seq & 0x3F)
```

- An inbound ML packet with `byte0 & 0x80` is MLR; route it by `(byte0 >> 4) & 7 == T & 7`.
- `seq` = sender's 6-bit sequence number (mod 64). `reqNum` = cumulative ack: the next seq the
  sender expects from the other side.
- Receiving: accept only `seq == nextRcv`, then `nextRcv = (nextRcv + 1) % 64`. Ack = header-only
  packet (no payload) with `reqNum = nextRcv`, `seq = 0`. We ack every 5 packets, or 250 ms after the
  last packet if nothing newer arrived. Out-of-order packet → immediately re-ack `nextRcv` so the watch
  retransmits.
- Throughput seen: ~227 payload bytes per packet, ~100 packets per 3.1 s ≈ **7.3 KB/s compressed**
  (≈ 14 KB/s of FIT). A 454 KB activity took 33 s.

### Compression (zlib)

- The transfer buffer is a **complete zlib stream** (RFC 1950): header `38 cb` (CM 8 deflate,
  CINFO 3 → **2 KB window**), deflate data, then **Adler-32 trailer (big-endian)** of the inflated
  bytes. Inflated length equals the listing's `size`; result starts with a normal FIT header
  (`.FIT` at bytes 8–11). Ratio ~52 %.
- Python `zlib.decompress(buf)` works on it as-is (the `.fit` samples were produced that way).
- Browser: `new DecompressionStream('deflate')` takes zlib format (header + Adler) — should work as-is.
- Apple: `NSData.decompressed(using: .zlib)` is **raw** deflate and **failed** on the full stream in
  the last run (files saved as `.deflate`). `sync.swift` now strips the 2-byte header before inflating,
  but that change is **untested**; the 4-byte Adler trailer may also need dropping. On iOS, the safest
  route is `libz` `inflateInit2(…, 15)` on the whole buffer (zlib format accepts smaller windows), or
  Compression framework on `buf[2 ..< count-4]`.

## 6. Quirks

- **FileResponse is re-sent every ~5 s** with the same handle while the transfer is pending or running.
  If you re-register 0x2018 / re-send the file request on each repeat, the watch restarts and you get
  **0 bytes** (run 21:50: two re-opens, then the watch closed the channel with nothing). Remember the
  pending handle and ignore repeats for it.
- **Non-reliable transfer stalls.** Registering 0x2018 with `reliable = 0` (`--plain-transfer`) is
  accepted, but nothing flowed for ~7 s until the 5 s FileResponse repeat made us re-open; then
  plain packets (no MLR header, no `00 00 00` start header) arrived every ~30 ms, the client of that
  run discarded them, and the next file's channel closed with 0 bytes. No flow control or retransmit
  exists without MLR, so we never got a complete file this way. Always use MLR.
- GFDI handle number changes every connection; the transfer handle also changes per file.
- The watch advertises without a name, so filter on manufacturer data; the GAP name may only show up
  after connecting.
- The final 5044 TransferStatusRequest can arrive interleaved with the next file's FileResponse; handle
  them by field, not by order.
- No pairing/auth prompt appeared on the paired Mac in these runs; `AUTH_NEGOTIATION` was never sent.
