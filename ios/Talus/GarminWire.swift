import Compression
import Foundation

/// The byte formats a Garmin watch speaks over Bluetooth: ML packets, COBS-framed GFDI messages,
/// protobuf FileSync and MLR. Pure functions, unit tested; `WatchLink` does the Bluetooth side.
/// tools/garmin-ble/NOTES.md has the protocol notes these follow.
enum GarminWire {
    static func mlUUID(_ n: Int) -> String { String(format: "6A4E%04X-667B-11E3-949A-0800200C9A66", n) }
    static let mlService = mlUUID(0x2800)
    static let garminCompany = 0x0087
    /// Gadgetbridge's ML client id; ML management messages are scoped to it.
    static let clientID: UInt64 = 2
    static let gfdiService = 0x0001
    static let fileService = 0x2018
    static let garminEpoch = Date(timeIntervalSince1970: 631_065_600)

    enum Mgmt: UInt8 { case registerReq = 0, registerResp, closeReq, closeResp, unkHandle, closeAllReq, closeAllResp }

    enum Msg: Int {
        case response = 5000, downloadRequest = 5002, fileTransferData = 5004, deviceInformation = 5024
        case protobufRequest = 5043, protobufResponse = 5044, configuration = 5050
        case currentTimeRequest = 5052, authNegotiation = 5101
    }

    // MARK: ML management (handle 0)

    static func closeAll() -> Data {
        var w = Writer(); w.u8(0); w.u8(Int(Mgmt.closeAllReq.rawValue)); w.u64(clientID); w.u16(0)
        return w.d
    }

    static func register(service: Int, reliable: Int) -> Data {
        var w = Writer(); w.u8(0); w.u8(Int(Mgmt.registerReq.rawValue)); w.u64(clientID); w.u16(service); w.u8(reliable)
        return w.d
    }

    enum Management: Equatable {
        case closeAll
        case registered(service: Int, status: Int, handle: Int, reliable: Int)
        case closed(service: Int, handle: Int)
        case other(Int)
    }

    /// Parses a management packet (handle byte already dropped). nil when it is for another client.
    static func management(_ d: Data) -> Management? {
        var r = Reader(d)
        guard r.remaining >= 9 else { return nil }
        let type = r.u8()
        let cid = UInt64(r.u32()) | UInt64(r.u32()) << 32
        guard cid == clientID else { return nil }
        switch Mgmt(rawValue: UInt8(type)) {
        case .closeAllResp: return .closeAll
        case .registerResp:
            guard r.remaining >= 4 else { return .other(type) }
            let svc = r.u16(), status = r.u8(), h = r.u8()
            return .registered(service: svc, status: status, handle: h, reliable: r.remaining > 0 ? r.u8() : 0)
        case .closeResp:
            guard r.remaining >= 3 else { return .other(type) }
            return .closed(service: r.u16(), handle: r.u8())
        default: return .other(type)
        }
    }

    // MARK: CRC, COBS, GFDI

    private static let crcTable: [UInt16] = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
                                             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]

    static func crc16(_ data: Data, seed: UInt16 = 0) -> UInt16 {
        var crc = seed
        for b in data {
            crc = ((crc >> 4) & 0x0FFF) ^ crcTable[Int(crc & 15)] ^ crcTable[Int(b & 15)]
            crc = ((crc >> 4) & 0x0FFF) ^ crcTable[Int(crc & 15)] ^ crcTable[Int((b >> 4) & 15)]
        }
        return crc
    }

    /// COBS with Garmin's framing: a zero before and after each frame.
    static func cobsEncode(_ data: Data) -> Data {
        var out = Data([0])
        let bytes = [UInt8](data)
        var i = 0
        var lastWasZero = false
        while i < bytes.count {
            var start = i
            while i < bytes.count && bytes[i] != 0 { i += 1 }
            lastWasZero = i < bytes.count
            var size = i - start
            while size >= 0xFE {
                out.append(0xFF); out.append(contentsOf: bytes[start..<start + 0xFE])
                size -= 0xFE; start += 0xFE
            }
            out.append(UInt8(size + 1)); out.append(contentsOf: bytes[start..<start + size])
            if lastWasZero { i += 1 }
        }
        if lastWasZero { out.append(0x01) }
        out.append(0)
        return out
    }

    struct CobsDecoder {
        private var buf = Data()

        mutating func feed(_ d: Data) -> [Data] {
            buf.append(d)
            var out: [Data] = []
            while true {
                guard buf.first == 0 else {
                    if let z = buf.firstIndex(of: 0) { buf.removeSubrange(buf.startIndex..<z); continue }
                    buf.removeAll(); return out
                }
                guard buf.count >= 2, let end = buf[(buf.startIndex + 1)...].firstIndex(of: 0) else { return out }
                let frame = [UInt8](buf[(buf.startIndex + 1)..<end])
                buf.removeSubrange(buf.startIndex...end)
                if frame.isEmpty { buf.insert(0, at: buf.startIndex); continue }
                var dec = Data(); var p = 0
                while p < frame.count {
                    let code = Int(frame[p]); p += 1
                    let n = min(code - 1, frame.count - p)
                    dec.append(contentsOf: frame[p..<p + n]); p += n
                    if code != 0xFF && p < frame.count { dec.append(0) }
                }
                out.append(dec)
            }
        }
    }

    /// A GFDI message: [length u16][type u16][body][crc u16], length counting the crc.
    static func gfdi(_ type: Int, _ body: (inout Writer) -> Void = { _ in }) -> Data {
        var w = Writer(); w.u16(0); w.u16(type); body(&w)
        var d = w.d
        let len = d.count + 2
        d[0] = UInt8(len & 0xFF); d[1] = UInt8(len >> 8)
        let c = crc16(d)
        d.append(UInt8(c & 0xFF)); d.append(UInt8(c >> 8))
        return d
    }

    static func ack(_ orig: Int, status: Int = 0) -> Data {
        gfdi(Msg.response.rawValue) { $0.u16(orig); $0.u8(status) }
    }

    /// Checks length and crc; returns the type (compact types expanded) and a reader at the body.
    static func parseGfdi(_ frame: Data) -> (type: Int, body: Reader)? {
        guard frame.count >= 6 else { return nil }
        var r = Reader(frame)
        let len = r.u16()
        guard len == frame.count else { return nil }
        let body = frame.prefix(len - 2)
        let crc = Int(frame[frame.startIndex + len - 2]) | Int(frame[frame.startIndex + len - 1]) << 8
        guard crc == Int(crc16(body)) else { return nil }
        var type = r.u16()
        if type & 0x8000 != 0 { type = (type & 0xFF) + 5000 }
        var b = Reader(body); b.p = 4
        return (type, b)
    }

    struct DeviceInfo: Equatable {
        let protocolVersion: Int
        let product: Int
        let unit: UInt32
        let software: Int
        let name: String
        let model: String
        var softwareText: String { "\(software / 100).\(String(format: "%02d", software % 100))" }
    }

    static func deviceInfo(_ r0: Reader) -> DeviceInfo? {
        var r = r0
        guard r.remaining >= 12 else { return nil }
        let proto = r.u16(), product = r.u16(), unit = UInt32(truncatingIfNeeded: r.u32()), sw = r.u16()
        _ = r.u16() // max packet
        _ = r.str() // Bluetooth name
        let name = r.str(), model = r.str()
        return DeviceInfo(protocolVersion: proto, product: product, unit: unit, software: sw, name: name, model: model)
    }

    /// Our answer to the watch's DeviceInformation, as Gadgetbridge sends it.
    static func deviceInfoReply(protocolVersion: Int, hostName: String) -> Data {
        gfdi(Msg.response.rawValue) { w in
            w.u16(Msg.deviceInformation.rawValue); w.u8(0)
            w.u16(150); w.u16(0xFFFF); w.u32(-1); w.u16(7791); w.u16(0xFFFF)
            w.str(hostName); w.str("Apple"); w.str("iPhone")
            w.u8(protocolVersion / 100 == 1 ? 1 : 0)
        }
    }

    /// Our capabilities: CONNECT_MOBILE_FIT_LINK, SYNC, DEVICE_INITIATES_SYNC, HOST_INITIATED_SYNC_REQUESTS.
    static func configuration(length n: Int) -> Data {
        var ours = Data(count: n); if n > 0 { ours[0] = 0b0011_1001 }
        return gfdi(Msg.configuration.rawValue) { $0.u8(n); $0.d.append(ours) }
    }

    // MARK: Protobuf over GFDI

    static func protobuf(type: Int = Msg.protobufRequest.rawValue, requestId: Int, _ payload: Data) -> Data {
        gfdi(type) { w in w.u16(requestId); w.u32(0); w.u32(payload.count); w.u32(payload.count); w.d.append(payload) }
    }

    /// The status a non-final protobuf chunk is acknowledged with: KEPT, no error.
    static func protobufChunkAck(type: Int, requestId: Int, offset: Int) -> Data {
        gfdi(Msg.response.rawValue) { w in w.u16(type); w.u8(0); w.u16(requestId); w.u32(offset); w.u8(0); w.u8(0) }
    }

    /// Wraps a FileSync message in GDI Smart field 43.
    static func fileSync(_ inner: ProtoWriter) -> Data { var smart = ProtoWriter(); smart.bytes(43, inner.d); return smart.d }

    static func fileListRequest(cursor: UInt64?) -> Data {
        var req = ProtoWriter()
        if let cursor { req.uint(1, cursor) }
        req.uint(7, 0)
        var svc = ProtoWriter(); svc.bytes(9, req.d)
        return fileSync(svc)
    }

    /// Asks for one file, echoing the File message from the listing (constants from Gadgetbridge).
    static func fileRequest(_ f: WatchFile) -> Data {
        var req = ProtoWriter(); req.bytes(1, f.raw); req.uint(2, 24); req.uint(3, 0); req.uint(4, 0); req.uint(5, 15)
        var svc = ProtoWriter(); svc.bytes(1, req.d)
        return fileSync(svc)
    }

    static func transferStatusResponse() -> Data {
        var resp = ProtoWriter(); resp.uint(1, 1)
        var svc = ProtoWriter(); svc.bytes(21, resp.d)
        return fileSync(svc)
    }

    enum FileSyncMessage {
        case list(files: [WatchFile], cursor: UInt64?)
        case fileResponse(status: Int, handle: Int)
        case transferStatusRequest
        case other([Int])
    }

    /// Decodes a Smart message; nil when it isn't FileSync. `names` carries the type-name table
    /// across list entries, since a name is sent once and later entries only give its id.
    static func fileSyncMessage(_ smart: Data, names: inout [Int: String]) -> FileSyncMessage? {
        guard let svc = Proto.first(Proto.decode(smart), 43) else { return nil }
        let f = Proto.decode(svc.data)
        if let lr = Proto.first(f, 10) {
            let l = Proto.decode(lr.data)
            let files = l.filter { $0.0 == 4 }.map { WatchFile.parse($0.1.data, names: &names) }
            return .list(files: files, cursor: Proto.first(l, 2)?.uint)
        }
        if let fr = Proto.first(f, 2) {
            let x = Proto.decode(fr.data)
            return .fileResponse(status: Int(Proto.first(x, 1)?.uint ?? 99), handle: Int(Proto.first(x, 3)?.uint ?? 0))
        }
        if Proto.first(f, 5) != nil { return .transferStatusRequest }
        return .other(f.map(\.0))
    }

    // MARK: MLR

    /// byte0 = 0x80 | (handle & 7) << 4 | reqNum >> 2, byte1 = (reqNum & 3) << 6 | seq.
    static func mlrPacket(handle: Int, req: Int, seq: Int, _ data: Data = Data()) -> Data {
        Data([UInt8(0x80 | (handle & 0x07) << 4 | (req >> 2) & 0x0F), UInt8((req & 0x03) << 6 | seq & 0x3F)]) + data
    }

    static func mlrHeader(_ p: Data) -> (handle: Int, req: Int, seq: Int)? {
        guard p.count >= 2 else { return nil }
        let b0 = Int(p[p.startIndex]), b1 = Int(p[p.startIndex + 1])
        guard b0 & 0x80 != 0 else { return nil }
        return ((b0 >> 4) & 0x07, (b0 & 0x0F) << 2 | (b1 >> 6) & 0x03, b1 & 0x3F)
    }

    /// The body that asks the file channel for a file handle from FileResponse.
    static func fileHandleRequest(_ handle: Int) -> Data {
        var w = Writer(); w.u8(0); w.u8(0); w.u16(handle); w.u8(0); w.u8(0)
        return w.d
    }

    // MARK: zlib

    enum InflateError: LocalizedError {
        case notZlib, corrupt, checksum
        var errorDescription: String? {
            switch self {
            case .notZlib: "The watch sent a file that isn't zlib-compressed."
            case .corrupt: "The file from the watch didn't decompress."
            case .checksum: "The file from the watch failed its checksum."
            }
        }
    }

    /// The watch sends each file as a zlib stream (2 KB window, Adler-32 trailer). Compression's
    /// ZLIB is raw deflate, so drop the 2-byte header and 4-byte trailer and check the Adler-32.
    static func inflate(_ z: Data, expectedSize: Int) throws -> Data {
        let b = [UInt8](z)
        guard b.count > 6, b[0] & 0x0F == 8, (Int(b[0]) << 8 | Int(b[1])) % 31 == 0, b[1] & 0x20 == 0 else { throw InflateError.notZlib }
        let raw = Array(b[2..<(b.count - 4)])
        var capacity = max(expectedSize, raw.count * 4) + 1024
        while true {
            var out = [UInt8](repeating: 0, count: capacity)
            let n = compression_decode_buffer(&out, capacity, raw, raw.count, nil, COMPRESSION_ZLIB)
            guard n > 0 else { throw InflateError.corrupt }
            if n == capacity && capacity < 1 << 30 { capacity *= 2; continue }
            let data = Data(out[0..<n])
            let trailer = UInt32(b[b.count - 4]) << 24 | UInt32(b[b.count - 3]) << 16 | UInt32(b[b.count - 2]) << 8 | UInt32(b[b.count - 1])
            guard adler32(data) == trailer else { throw InflateError.checksum }
            return data
        }
    }

    static func adler32(_ d: Data) -> UInt32 {
        var a: UInt32 = 1, b: UInt32 = 0
        var i = d.startIndex
        while i < d.endIndex {
            let end = min(i + 5552, d.endIndex)
            for x in d[i..<end] { a += UInt32(x); b += a }
            a %= 65521; b %= 65521
            i = end
        }
        return b << 16 | a
    }

    static func isFit(_ d: Data) -> Bool {
        d.count > 12 && d[d.startIndex + 8..<d.startIndex + 12] == Data(".FIT".utf8)
    }

    // MARK: Byte helpers

    struct Writer {
        var d = Data()
        mutating func u8(_ v: Int) { d.append(UInt8(truncatingIfNeeded: v)) }
        mutating func u16(_ v: Int) { withUnsafeBytes(of: UInt16(truncatingIfNeeded: v).littleEndian) { d.append(contentsOf: $0) } }
        mutating func u32(_ v: Int) { withUnsafeBytes(of: UInt32(truncatingIfNeeded: v).littleEndian) { d.append(contentsOf: $0) } }
        mutating func u64(_ v: UInt64) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        mutating func str(_ s: String) { let b = Array(s.utf8.prefix(255)); u8(b.count); d.append(contentsOf: b) }
    }

    /// Bounds-checked: reads past the end give zeros instead of trapping on a short message.
    struct Reader {
        let d: [UInt8]
        var p = 0
        init(_ data: Data) { d = [UInt8](data) }
        var remaining: Int { max(0, d.count - p) }
        private func at(_ i: Int) -> Int { i < d.count ? Int(d[i]) : 0 }
        mutating func u8() -> Int { defer { p += 1 }; return at(p) }
        mutating func u16() -> Int { defer { p += 2 }; return at(p) | at(p + 1) << 8 }
        mutating func u32() -> Int { defer { p += 4 }; return at(p) | at(p + 1) << 8 | at(p + 2) << 16 | at(p + 3) << 24 }
        mutating func bytes(_ n: Int) -> Data {
            let n = max(0, min(n, remaining)); defer { p += n }
            return Data(d[p..<p + n])
        }
        mutating func str() -> String { String(decoding: bytes(u8()), as: UTF8.self) }
    }
}

/// A file in the watch's FileSync listing.
struct WatchFile: Equatable {
    /// The File message as the watch sent it; echoed back to ask for the file.
    let raw: Data
    let id1: UInt64
    let id2: UInt64
    let typeName: String?
    let typeCode: Int?
    /// Uncompressed size.
    let size: Int
    let timestamp: UInt32

    var kind: String { typeName ?? "code\(typeCode ?? -1)" }
    var date: Date? { timestamp == 0 ? nil : GarminWire.garminEpoch.addingTimeInterval(TimeInterval(timestamp)) }
    /// Matches the web app's key so either app skips what the other already imported.
    var key: String { "\(kind)-\(String(format: "%016llx", id1))-\(String(format: "%016llx", id2))" }

    var fileName: String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd-HH-mm-ss"
        return "\(kind)_\(date.map(f.string) ?? "nodate")_\(String(format: "%016llx", id2)).fit"
    }

    static func parse(_ d: Data, names: inout [Int: String]) -> WatchFile {
        let f = Proto.decode(d)
        let id = Proto.decode(Proto.first(f, 1)?.data ?? Data())
        let t = Proto.decode(Proto.first(f, 2)?.data ?? Data())
        let nameId = Proto.first(t, 3).map { Int($0.uint) }
        var name = Proto.first(t, 2).map { String(decoding: $0.data, as: UTF8.self) }
        if let n = name, let i = nameId { names[i] = n }
        if name == nil, let i = nameId { name = names[i] }
        return WatchFile(raw: d, id1: Proto.first(id, 1)?.uint ?? 0, id2: Proto.first(id, 2)?.uint ?? 0,
                         typeName: name, typeCode: Proto.first(t, 1).map { Int($0.uint) },
                         size: Int(Proto.first(f, 3)?.uint ?? 0),
                         timestamp: UInt32(truncatingIfNeeded: Proto.first(f, 7)?.uint ?? 0))
    }
}

/// Just enough of the protobuf wire format for FileSync.
enum Proto {
    enum Value {
        case varint(UInt64), fixed64(UInt64), bytes(Data), fixed32(UInt32)
        var uint: UInt64 {
            switch self {
            case .varint(let v), .fixed64(let v): v
            case .fixed32(let v): UInt64(v)
            case .bytes: 0
            }
        }
        var data: Data { if case .bytes(let b) = self { return b }; return Data() }
    }

    static func decode(_ data: Data) -> [(Int, Value)] {
        let b = [UInt8](data); var i = 0; var out: [(Int, Value)] = []
        func varint() -> UInt64? {
            var v: UInt64 = 0; var shift: UInt64 = 0
            while i < b.count && shift < 64 { let x = b[i]; i += 1; v |= UInt64(x & 0x7F) << shift; if x & 0x80 == 0 { return v }; shift += 7 }
            return nil
        }
        func fixed(_ n: Int) -> UInt64 { var v: UInt64 = 0; for k in 0..<n { v |= UInt64(b[i + k]) << (8 * UInt64(k)) }; i += n; return v }
        while i < b.count {
            guard let key = varint() else { break }
            let field = Int(key >> 3)
            switch key & 7 {
            case 0: guard let v = varint() else { return out }; out.append((field, .varint(v)))
            case 1: guard i + 8 <= b.count else { return out }; out.append((field, .fixed64(fixed(8))))
            case 2:
                guard let n = varint(), n <= UInt64(b.count - i) else { return out }
                out.append((field, .bytes(Data(b[i..<i + Int(n)])))); i += Int(n)
            case 5: guard i + 4 <= b.count else { return out }; out.append((field, .fixed32(UInt32(fixed(4)))))
            default: return out
            }
        }
        return out
    }

    static func first(_ fields: [(Int, Value)], _ f: Int) -> Value? { fields.first { $0.0 == f }?.1 }
}

struct ProtoWriter {
    var d = Data()
    mutating func varint(_ v: UInt64) { var v = v; while v >= 0x80 { d.append(UInt8(v & 0x7F) | 0x80); v >>= 7 }; d.append(UInt8(v)) }
    mutating func uint(_ f: Int, _ v: UInt64) { varint(UInt64(f << 3)); varint(v) }
    mutating func bytes(_ f: Int, _ b: Data) { varint(UInt64(f << 3 | 2)); varint(UInt64(b.count)); d.append(b) }
}
