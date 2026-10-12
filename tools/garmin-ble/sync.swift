// Read-only Garmin BLE file pull over the ML/GFDI protocol, after Gadgetbridge's
// CommunicatorV2 / FileTransferHandler. Never deletes, archives or flags files on the
// watch, never answers auth negotiation, never changes watch settings or time.
//
// usage: garmin-ble-sync [--name "fenix 9"] [--identify] [--list] [--download N] [--out DIR] [--timeout SEC]
import Foundation
import CoreBluetooth

// MARK: - options

var nameFilter = ""
var downloadPerType = 0
var outDir = NSString(string: "~/Developer/garmin-watch-sample").expandingTildeInPath
var timeoutSec = 180.0
var identifyOnly = false
var gbFlags = false
var plainTransfer = false
do {
    var args = Array(CommandLine.arguments.dropFirst())
    while !args.isEmpty {
        let a = args.removeFirst()
        switch a {
        case "--name": nameFilter = args.removeFirst().lowercased()
        case "--list": downloadPerType = 0
        case "--identify": identifyOnly = true
        case "--gb-flags": gbFlags = true
        case "--plain-transfer": plainTransfer = true
        case "--download": downloadPerType = Int(args.removeFirst()) ?? 2
        case "--out": outDir = NSString(string: args.removeFirst()).expandingTildeInPath
        case "--timeout": timeoutSec = Double(args.removeFirst()) ?? 180
        default: print("unknown arg \(a)"); exit(2)
        }
    }
}

func log(_ s: String) {
    let f = DateFormatter(); f.dateFormat = "HH:mm:ss.SSS"
    print("[\(f.string(from: Date()))] \(s)")
    fflush(stdout)
}
func hex(_ d: Data) -> String { d.map { String(format: "%02x", $0) }.joined() }

// MARK: - protocol constants

func mlUUID(_ n: Int) -> CBUUID { CBUUID(string: String(format: "6A4E%04X-667B-11E3-949A-0800200C9A66", n)) }
let mlService = mlUUID(0x2800)
let clientID: UInt64 = 2 // Gadgetbridge's ML client id

enum Req: UInt8 { case registerReq = 0, registerResp, closeReq, closeResp, unkHandle, closeAllReq, closeAllResp }

enum Msg: Int {
    case response = 5000, downloadRequest = 5002, fileTransferData = 5004, filter = 5007
    case setFileFlag = 5008, fileAvailable = 5009, batteryStatus = 5023, deviceInformation = 5024
    case systemEvent = 5030, supportedFileTypes = 5031, synchronization = 5037
    case protobufRequest = 5043, protobufResponse = 5044, configuration = 5050
    case currentTimeRequest = 5052, authNegotiation = 5101
}

let fitSubtypes: [Int: String] = [
    1: "Device", 2: "Settings", 3: "Sports", 4: "Activity", 5: "Workouts", 6: "Courses", 7: "Schedules",
    9: "Weight", 10: "Totals", 11: "Goals", 15: "MonitorA", 20: "Summary", 28: "MonitorDaily", 29: "Records",
    32: "Monitor", 34: "Segments", 35: "SegmentList", 41: "Changelog", 44: "Metrics", 49: "Sleep",
    52: "UserBehaviorLog", 57: "SportsBackup", 58: "Device58", 61: "ECG", 66: "FitType66",
    68: "HRVStatus", 70: "HSA", 71: "ComAct", 72: "FBTBackup", 73: "SkinTemp", 74: "FBTPTDBackup", 77: "Schedule",
]
// Folders the thread asked for, plus a few other health files.
let wantedSubtypes: Set<Int> = [4, 32, 49, 68, 44, 28, 15, 73, 70]

// MARK: - CRC / COBS / GFDI framing

let crcTable: [UInt16] = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
                          0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
func crc16(_ data: Data, seed: UInt16 = 0) -> UInt16 {
    var crc = seed
    for b in data {
        crc = ((crc >> 4) & 0x0FFF) ^ crcTable[Int(crc & 15)] ^ crcTable[Int(b & 15)]
        crc = ((crc >> 4) & 0x0FFF) ^ crcTable[Int(crc & 15)] ^ crcTable[Int((b >> 4) & 15)]
    }
    return crc
}

func cobsEncode(_ data: Data) -> Data {
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
    var buf = Data()
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

struct W {
    var d = Data()
    mutating func u8(_ v: Int) { d.append(UInt8(truncatingIfNeeded: v)) }
    mutating func u16(_ v: Int) { withUnsafeBytes(of: UInt16(truncatingIfNeeded: v).littleEndian) { d.append(contentsOf: $0) } }
    mutating func u32(_ v: Int) { withUnsafeBytes(of: UInt32(truncatingIfNeeded: v).littleEndian) { d.append(contentsOf: $0) } }
    mutating func u64(_ v: UInt64) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
    mutating func str(_ s: String) { let b = Array(s.utf8.prefix(255)); u8(b.count); d.append(contentsOf: b) }
}

struct R {
    let d: [UInt8]; var p = 0
    init(_ data: Data) { d = [UInt8](data) }
    var remaining: Int { d.count - p }
    mutating func u8() -> Int { defer { p += 1 }; return Int(d[p]) }
    mutating func u16() -> Int { defer { p += 2 }; return Int(d[p]) | Int(d[p + 1]) << 8 }
    mutating func u32() -> Int { defer { p += 4 }; return Int(d[p]) | Int(d[p + 1]) << 8 | Int(d[p + 2]) << 16 | Int(d[p + 3]) << 24 }
    mutating func bytes(_ n: Int) -> Data { defer { p += n }; return Data(d[p..<p + n]) }
    mutating func str() -> String { let n = u8(); return String(decoding: bytes(n), as: UTF8.self) }
}

/// Builds a GFDI message: [len u16][type u16][body][crc u16], len includes the crc.
func gfdi(_ type: Int, _ body: (inout W) -> Void) -> Data {
    var w = W(); w.u16(0); w.u16(type); body(&w)
    var d = w.d
    let len = d.count + 2
    d[0] = UInt8(len & 0xFF); d[1] = UInt8(len >> 8)
    let c = crc16(d)
    d.append(UInt8(c & 0xFF)); d.append(UInt8(c >> 8))
    return d
}

func ack(_ orig: Int, status: Int = 0) -> Data {
    gfdi(Msg.response.rawValue) { $0.u16(orig); $0.u8(status) }
}

let garminEpoch = Date(timeIntervalSince1970: 631065600)

// MARK: - directory

struct Entry {
    let index, dataType, subType, fileNumber, specificFlags, fileFlags, size, ts: Int
    var date: Date? { ts == 0 ? nil : garminEpoch.addingTimeInterval(TimeInterval(UInt32(truncatingIfNeeded: ts))) }
    var kind: String { dataType == 128 ? (fitSubtypes[subType] ?? "FIT_\(subType)") : "type\(dataType)_\(subType)" }
}

// MARK: - client

final class Client: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    var central: CBCentralManager!
    var peripheral: CBPeripheral?
    var sendChar: CBCharacteristic?
    var recvChar: CBCharacteristic?
    var gfdiHandle: Int?
    var decoder = CobsDecoder()
    var writeQueue: [Data] = []
    var writing = false

    // download state
    var dlQueue: [Entry] = []
    var current: Entry?          // nil index 0 => directory
    var currentIsDirectory = false
    var buffer = Data()
    var expected = 0
    var runningCrc: UInt16 = 0
    var directory: [Entry] = []
    var handshakeDone = false
    var savedFiles: [String] = []

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + timeoutSec) { [weak self] in
            log("timeout after \(Int(timeoutSec))s")
            self?.finish(code: 3)
        }
    }

    func finish(code: Int32) {
        if let p = peripheral { central.cancelPeripheralConnection(p) }
        if !savedFiles.isEmpty { log("saved \(savedFiles.count) files under \(outDir)") }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { exit(code) }
    }

    // MARK: discovery / connect

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        log("bluetooth state=\(c.state.rawValue) auth=\(CBManager.authorization.rawValue)")
        guard c.state == .poweredOn else { return }
        let already = c.retrieveConnectedPeripherals(withServices: [mlService])
        log("already-connected ML peripherals: \(already.map { $0.name ?? "?" })")
        if let p = already.first(where: matches) { connect(p); return }
        c.scanForPeripherals(withServices: nil, options: nil)
        log("scanning for Garmin (name filter: \"\(nameFilter)\")")
    }

    func matches(_ p: CBPeripheral) -> Bool {
        nameFilter.isEmpty || (p.name ?? "").lowercased().contains(nameFilter)
    }

    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral, advertisementData ad: [String: Any], rssi: NSNumber) {
        let name = (ad[CBAdvertisementDataLocalNameKey] as? String) ?? p.name ?? ""
        let svcs = (ad[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID]) ?? []
        let mfg = (ad[CBAdvertisementDataManufacturerDataKey] as? Data) ?? Data()
        let garmin = svcs.contains(mlService) || (mfg.count >= 2 && mfg[0] == 0x87 && mfg[1] == 0) || name.lowercased().contains("fenix")
        guard garmin else { return }
        log("found \(name.isEmpty ? "(no name)" : name) rssi=\(rssi) services=\(svcs) mfg=\(hex(mfg))")
        // Garmin often advertises without a name; the name filter is applied after connecting.
        guard name.isEmpty || nameFilter.isEmpty || name.lowercased().contains(nameFilter) else { return }
        c.stopScan()
        connect(p)
    }

    func connect(_ p: CBPeripheral) {
        peripheral = p
        p.delegate = self
        log("connecting to \(p.name ?? "?") \(p.identifier)")
        central.connect(p, options: nil)
    }

    func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
        log("connected; discovering ML service")
        p.discoverServices(nil)
    }

    func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        log("connect failed: \(error?.localizedDescription ?? "?")"); finish(code: 1)
    }

    func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
        log("disconnected: \(error?.localizedDescription ?? "clean")"); finish(code: error == nil ? 0 : 1)
    }

    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        for s in p.services ?? [] { log("service \(s.uuid)") }
        guard p.services?.contains(where: { $0.uuid == mlService }) == true else {
            log("no ML service 6A4E2800 on this device"); finish(code: 1); return
        }
        // Identify the watch first from the GAP name / Device Information model, before
        // touching the ML service (whose notify is what may trigger pairing).
        if let dis = p.services?.first(where: { $0.uuid == CBUUID(string: "180A") }) {
            p.discoverCharacteristics([CBUUID(string: "2A24"), CBUUID(string: "2A29"), CBUUID(string: "2A26")], for: dis)
        } else {
            identified(model: "")
        }
    }

    var disPending = 0
    var model = ""
    func identified(model m: String) {
        let ident = "\(peripheral?.name ?? "") \(m)".trimmingCharacters(in: .whitespaces)
        log("identified as \"\(ident)\"")
        if !nameFilter.isEmpty && !ident.lowercased().contains(nameFilter) {
            log("not the requested watch (\"\(nameFilter)\"); disconnecting before ML/pairing")
            finish(code: 5); return
        }
        if identifyOnly { log("--identify: stopping before ML/pairing"); finish(code: 0); return }
        guard let p = peripheral, let ml = p.services?.first(where: { $0.uuid == mlService }) else { return }
        p.discoverCharacteristics(nil, for: ml)
    }


    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        if s.uuid == CBUUID(string: "180A") {
            let chars = s.characteristics ?? []
            disPending = chars.count
            if chars.isEmpty { identified(model: "") }
            for ch in chars { p.readValue(for: ch) }
            return
        }
        if let e = error { log("characteristic discovery error: \(e.localizedDescription)") }
        let chars = s.characteristics ?? []
        for ch in chars { log("  char \(ch.uuid) props=0x\(String(ch.properties.rawValue, radix: 16))") }
        for i in 0x2810...0x2814 {
            if let r = chars.first(where: { $0.uuid == mlUUID(i) }), let w = chars.first(where: { $0.uuid == mlUUID(i + 0x10) }) {
                recvChar = r; sendChar = w
                log("using recv \(r.uuid) / send \(w.uuid); enabling notifications (macOS may ask to pair here)")
                p.setNotifyValue(true, for: r)
                return
            }
        }
        log("no known ML characteristic pair"); finish(code: 1)
    }

    func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor ch: CBCharacteristic, error: Error?) {
        if let e = error { log("notify failed: \(e.localizedDescription)"); finish(code: 1); return }
        log("notifications on; max write (no rsp) = \(p.maximumWriteValueLength(for: .withoutResponse))")
        var w = W(); w.u8(0); w.u8(Int(Req.closeAllReq.rawValue)); w.u64(clientID); w.u16(0)
        rawWrite(w.d)
    }

    // MARK: raw ML writes

    var writeType: CBCharacteristicWriteType {
        (sendChar?.properties.contains(.write) ?? false) ? .withResponse : .withoutResponse
    }

    func rawWrite(_ d: Data) { writeQueue.append(d); pump() }

    func pump() {
        guard !writing, !writeQueue.isEmpty, let p = peripheral, let ch = sendChar else { return }
        let d = writeQueue.removeFirst()
        if writeType == .withResponse {
            writing = true
            p.writeValue(d, for: ch, type: .withResponse)
        } else {
            p.writeValue(d, for: ch, type: .withoutResponse)
            DispatchQueue.main.async { self.pump() }
        }
    }

    func peripheral(_ p: CBPeripheral, didWriteValueFor ch: CBCharacteristic, error: Error?) {
        if let e = error { log("write error: \(e.localizedDescription)") }
        writing = false; pump()
    }

    func sendGfdi(_ msg: Data) {
        guard let h = gfdiHandle, let p = peripheral else { return }
        let enc = cobsEncode(msg)
        let chunk = max(19, p.maximumWriteValueLength(for: writeType) - 1)
        var i = 0
        while i < enc.count {
            let part = enc[i..<min(i + chunk, enc.count)]
            rawWrite(Data([UInt8(h)]) + part)
            i += chunk
        }
    }

    // MARK: inbound

    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        if ch.service?.uuid == CBUUID(string: "180A") {
            let v = ch.value.map { String(decoding: $0, as: UTF8.self) } ?? "(\(error?.localizedDescription ?? "no value"))"
            log("DIS \(ch.uuid): \(v)")
            if ch.uuid == CBUUID(string: "2A24"), error == nil { model = v }
            disPending -= 1
            if disPending == 0 { identified(model: model) }
            return
        }
        guard let v = ch.value, !v.isEmpty else { return }
        let handle = Int(v[0])
        if handle & 0x80 != 0 {
            if let th = fs.transferHandle, fs.mlr, (handle & 0x70) >> 4 == th & 0x07 { mlrReceive(v); return }
            log("MLR packet for unknown handle: \(hex(v.prefix(16)))"); return
        }
        if handle == 0 { handleManagement(R(v.dropFirst())); return }
        if handle == fs.transferHandle { transferData(v.dropFirst()); return }
        guard handle == gfdiHandle else { log("data on unknown handle \(handle): \(hex(v))"); return }
        for frame in decoder.feed(v.dropFirst()) { onGfdi(frame) }
    }

    func handleManagement(_ r0: R) {
        var r = r0
        let type = r.u8()
        let cid = UInt64(r.u32()) | UInt64(r.u32()) << 32
        guard cid == clientID else { log("handle mgmt for other client \(cid)"); return }
        switch Req(rawValue: UInt8(type)) {
        case .closeAllResp:
            log("close-all ok; registering GFDI (plain ML, no MLR)")
            var w = W(); w.u8(0); w.u8(Int(Req.registerReq.rawValue)); w.u64(clientID); w.u16(1); w.u8(0)
            rawWrite(w.d)
        case .registerResp:
            let svc = r.u16(), status = r.u8()
            guard status == 0 else { log("register service \(svc) failed status=\(status)"); finish(code: 1); return }
            let h = r.u8(), reliable = r.remaining > 0 ? r.u8() : 0
            log("registered service 0x\(String(svc, radix: 16)) on handle \(h) reliable=\(reliable)")
            if svc == 1 { gfdiHandle = h; log("waiting for watch's DeviceInformation") }
            if svc == 0x2018 { transferOpened(handle: h, reliable: reliable) }
        case .closeResp:
            let svc = r.u16(), h = r.u8()
            log("watch closed service 0x\(String(svc, radix: 16)) handle \(h)")
            if svc == 0x2018 { transferClosed() }
        default:
            log("handle mgmt type \(type): \(r.d)")
        }
    }

    func onGfdi(_ frame: Data) {
        guard frame.count >= 6 else { log("short GFDI frame \(hex(frame))"); return }
        var r = R(frame)
        let len = r.u16()
        guard len == frame.count else { log("bad GFDI length \(len) vs \(frame.count)"); return }
        let crc = Int(frame[len - 2]) | Int(frame[len - 1]) << 8
        guard crc == Int(crc16(frame.prefix(len - 2))) else { log("bad GFDI crc"); return }
        var type = r.u16()
        if type & 0x8000 != 0 { type = (type & 0xFF) + 5000 }
        r = R(frame.prefix(len - 2)); r.p = 4
        switch Msg(rawValue: type) {
        case .deviceInformation: onDeviceInfo(r)
        case .configuration: onConfiguration(r)
        case .response: onResponse(r)
        case .fileTransferData: onFileData(r)
        case .protobufRequest, .protobufResponse:
            onProtobuf(type, r)
        case .authNegotiation:
            log("AUTH_NEGOTIATION received (\(hex(frame))). Guardrail: not answering; stopping.")
            finish(code: 4)
        case .currentTimeRequest:
            log("watch asked for current time; replying UNSUPPORTED (no time change)")
            sendGfdi(ack(type, status: 2))
        case .some(let m):
            log("watch sent \(m) (\(type)) \(r.remaining)B; ACK")
            sendGfdi(ack(type))
        case .none:
            log("watch sent unhandled type \(type) \(r.remaining)B: \(hex(r.bytes(min(r.remaining, 48)))); no reply")
        }
    }

    func onDeviceInfo(_ r0: R) {
        var r = r0
        let proto = r.u16(), product = r.u16(), unit = UInt32(truncatingIfNeeded: r.u32()), sw = r.u16(), maxPkt = r.u16()
        let btName = r.str(), devName = r.str(), model = r.str()
        log("DEVICE: protocol=\(proto) product=\(product) unit=\(unit) sw=\(sw / 100).\(String(format: "%02d", sw % 100)) maxPacket=\(maxPkt) bt=\"\(btName)\" name=\"\(devName)\" model=\"\(model)\"")
        let reply = gfdi(Msg.response.rawValue) { w in
            w.u16(Msg.deviceInformation.rawValue); w.u8(0)
            w.u16(150); w.u16(0xFFFF); w.u32(-1); w.u16(7791); w.u16(0xFFFF)
            w.str(Host.current().localizedName ?? "Talus"); w.str("Apple"); w.str("Mac")
            w.u8(proto / 100 == 1 ? 1 : 0)
        }
        sendGfdi(reply)
    }

    func onConfiguration(_ r0: R) {
        var r = r0
        let n = r.u8()
        let caps = r.bytes(n)
        log("watch capabilities (\(n)B): \(hex(caps))")
        sendGfdi(ack(Msg.configuration.rawValue))
        // Ours: CONNECT_MOBILE_FIT_LINK, SYNC, DEVICE_INITIATES_SYNC, HOST_INITIATED_SYNC_REQUESTS only.
        var ours = Data(count: n); if n > 0 { ours[0] = 0b0011_1001 }
        sendGfdi(gfdi(Msg.configuration.rawValue) { $0.u8(n); $0.d.append(ours) })
        handshakeDone = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { self.requestDirectory() }
    }

    func requestDirectory() {
        log("requesting file directory (file index 0)")
        startDownload(index: 0, directory: true)
    }

    func startDownload(index: Int, directory: Bool) {
        currentIsDirectory = directory
        buffer = Data(); expected = 0; runningCrc = 0
        sendGfdi(gfdi(Msg.downloadRequest.rawValue) { w in
            w.u16(index); w.u32(0); w.u8(1 /* NEW */); w.u16(0); w.u32(0); w.u8(0)
        })
    }

    func onResponse(_ r0: R) {
        var r = r0
        let orig = r.u16(), status = r.u8()
        if orig == Msg.downloadRequest.rawValue {
            guard status == 0 else { log("download request NAK status=\(status)"); next(); return }
            let dl = r.u8(), size = r.u32()
            let names = ["OK", "INDEX_UNKNOWN", "INDEX_NOT_READABLE", "NO_SPACE_LEFT", "INVALID", "NOT_READY", "CRC_INCORRECT"]
            log("download status \(dl < names.count ? names[dl] : "\(dl)") size=\(size)")
            guard dl == 0 else { next(); return }
            expected = size
            if size == 0 { complete() }
        } else {
            log("response for \(orig): status=\(status) extra=\(hex(r.bytes(r.remaining)))")
        }
    }

    func onFileData(_ r0: R) {
        var r = r0
        _ = r.u8()
        let crc = r.u16(), offset = r.u32()
        let chunk = r.bytes(r.remaining)
        guard offset == buffer.count else {
            log("offset mismatch \(offset) vs \(buffer.count)")
            sendGfdi(gfdi(Msg.response.rawValue) { $0.u16(Msg.fileTransferData.rawValue); $0.u8(0); $0.u8(4); $0.u32(self.buffer.count) })
            return
        }
        let c = crc16(chunk, seed: runningCrc)
        guard Int(c) == crc else {
            log("chunk crc mismatch at \(offset)")
            sendGfdi(gfdi(Msg.response.rawValue) { $0.u16(Msg.fileTransferData.rawValue); $0.u8(0); $0.u8(3); $0.u32(self.buffer.count) })
            return
        }
        runningCrc = c
        buffer.append(chunk)
        sendGfdi(gfdi(Msg.response.rawValue) { $0.u16(Msg.fileTransferData.rawValue); $0.u8(0); $0.u8(0); $0.u32(self.buffer.count) })
        if buffer.count >= expected { complete() }
    }

    func complete() {
        if currentIsDirectory { parseDirectory() } else { save() }
        next()
    }

    func parseDirectory() {
        guard buffer.count % 16 == 0 else { log("odd directory length \(buffer.count): \(hex(buffer.prefix(64)))"); return }
        var r = R(buffer)
        directory = []
        while r.remaining >= 16 {
            directory.append(Entry(index: r.u16(), dataType: r.u8(), subType: r.u8(), fileNumber: r.u16(),
                                   specificFlags: r.u8(), fileFlags: r.u8(), size: r.u32(), ts: r.u32()))
        }
        let df = ISO8601DateFormatter()
        print("\nDIRECTORY: \(directory.count) entries")
        print("index  type/sub  kind              fileNo  flags   size      date")
        for e in directory.sorted(by: { ($0.kind, $0.ts) < ($1.kind, $1.ts) }) {
            print(String(format: "%5d  %3d/%-3d   %@ %6d  %02x/%02x  %8d  %@",
                         e.index, e.dataType, e.subType, e.kind.padding(toLength: 16, withPad: " ", startingAt: 0) as NSString,
                         e.fileNumber, e.specificFlags, e.fileFlags, e.size, (e.date.map { df.string(from: $0) } ?? "-") as NSString))
        }
        print("")
        fflush(stdout)
        let dirLog = (outDir as NSString).appendingPathComponent("directory.txt")
        try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
        let lines = directory.map { "\($0.index)\t\($0.dataType)/\($0.subType)\t\($0.kind)\t\($0.fileNumber)\t\($0.size)\t\($0.date.map { df.string(from: $0) } ?? "-")" }
        try? lines.joined(separator: "\n").write(toFile: dirLog, atomically: true, encoding: .utf8)
        if !directory.contains(where: { $0.dataType == 128 }) {
            log("legacy directory has no FIT files; this watch uses the protobuf FileSync service")
            fs.useNewSync = true
            return
        }
        guard downloadPerType > 0 else { return }
        let fits = directory.filter { $0.dataType == 128 && wantedSubtypes.contains($0.subType) && $0.size > 0 }
        for (_, group) in Dictionary(grouping: fits, by: { $0.subType }) {
            dlQueue += group.sorted { $0.ts > $1.ts }.prefix(downloadPerType)
        }
        log("queued \(dlQueue.count) files for download (newest \(downloadPerType) per kind)")
    }

    func save() {
        guard let e = current else { return }
        let dir = (outDir as NSString).appendingPathComponent(e.kind)
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd-HH-mm-ss"; f.timeZone = TimeZone(identifier: "UTC")
        let name = "\(e.date.map { f.string(from: $0) } ?? "nodate")_\(e.index).fit"
        let path = (dir as NSString).appendingPathComponent(name)
        do {
            try buffer.write(to: URL(fileURLWithPath: path))
            if let d = e.date { try? FileManager.default.setAttributes([.modificationDate: d], ofItemAtPath: path) }
            savedFiles.append(path)
            log("saved \(e.kind)/\(name) (\(buffer.count) B)")
        } catch { log("save failed: \(error)") }
    }

    func next() {
        current = nil
        if fs.useNewSync { newSyncNext(); return }
        guard !dlQueue.isEmpty else {
            log("done; nothing marked, archived or deleted on the watch")
            finish(code: 0); return
        }
        let e = dlQueue.removeFirst()
        current = e
        log("downloading \(e.kind) index=\(e.index) size=\(e.size)")
        startDownload(index: e.index, directory: false)
    }
}


// MARK: - protobuf (minimal proto2 wire format)

enum PB {
    case varint(UInt64), fixed64(UInt64), bytes(Data), fixed32(UInt32)
    var u: UInt64 { switch self { case .varint(let v), .fixed64(let v): return v; case .fixed32(let v): return UInt64(v); default: return 0 } }
    var d: Data { if case .bytes(let b) = self { return b }; return Data() }
}

func pbDecode(_ data: Data) -> [(Int, PB)] {
    let b = [UInt8](data); var i = 0; var out: [(Int, PB)] = []
    func varint() -> UInt64? {
        var v: UInt64 = 0; var shift: UInt64 = 0
        while i < b.count { let x = b[i]; i += 1; v |= UInt64(x & 0x7F) << shift; if x & 0x80 == 0 { return v }; shift += 7 }
        return nil
    }
    while i < b.count {
        guard let key = varint() else { break }
        let field = Int(key >> 3)
        switch key & 7 {
        case 0: guard let v = varint() else { return out }; out.append((field, .varint(v)))
        case 1: guard i + 8 <= b.count else { return out }; var v: UInt64 = 0; for k in 0..<8 { v |= UInt64(b[i + k]) << (8 * UInt64(k)) }; i += 8; out.append((field, .fixed64(v)))
        case 2: guard let n = varint(), i + Int(n) <= b.count else { return out }; out.append((field, .bytes(Data(b[i..<i + Int(n)])))); i += Int(n)
        case 5: guard i + 4 <= b.count else { return out }; var v: UInt32 = 0; for k in 0..<4 { v |= UInt32(b[i + k]) << (8 * UInt32(k)) }; i += 4; out.append((field, .fixed32(v)))
        default: return out
        }
    }
    return out
}

struct PBW {
    var d = Data()
    mutating func varint(_ v: UInt64) { var v = v; while v >= 0x80 { d.append(UInt8(v & 0x7F) | 0x80); v >>= 7 }; d.append(UInt8(v)) }
    mutating func uint(_ f: Int, _ v: UInt64) { varint(UInt64(f << 3)); varint(v) }
    mutating func bytes(_ f: Int, _ b: Data) { varint(UInt64(f << 3 | 2)); varint(UInt64(b.count)); d.append(b) }
}

func pbFirst(_ fields: [(Int, PB)], _ f: Int) -> PB? { fields.first { $0.0 == f }?.1 }

struct SyncFile {
    let raw: Data           // the File message as the watch sent it, echoed back in FileRequest
    let id1, id2: UInt64
    let typeCode: Int?
    var typeName: String?
    let nameId: Int?
    let size: UInt64
    let pageId: Int?
    let timestamp: UInt32
    var date: Date? { timestamp == 0 ? nil : garminEpoch.addingTimeInterval(TimeInterval(timestamp)) }
    var kind: String { typeName ?? (nameId.map { "nameId\($0)" } ?? "code\(typeCode ?? -1)") }
}

// MARK: - FileSync state

final class FSState {
    var useNewSync = false
    var listing = false
    var listed = false
    var files: [SyncFile] = []
    var nameById: [Int: String] = [:]
    var queue: [SyncFile] = []
    var current: SyncFile?
    var requestId = 0
    var chunks: [Int: Data] = [:]
    var transferHandle: Int?
    var transferStarted = false
    var transferBuf = Data()
    var pendingFileHandle: Int?
    var mlr = false
    var nextRcv = 0
    var lastSentAck = 0
    var reqAcked = false
    var packets = 0
}
let fs = FSState()

// Kinds to sample; matched case-insensitively against the watch's type names.
// The watch names FIT kinds "FIT_TYPE_<subtype>" (4 activity, 32 monitor, 49 sleep, 68 HRV status,
// 44 metrics, 28 monitor daily, 15 monitor A, 73 skin temp, 70 HSA).
let wantedKinds: Set<String> = Set([4, 32, 49, 68, 44, 28, 15, 73, 70].map { "fit_type_\($0)" })

extension Client {
    func nextRequestId() -> Int { fs.requestId = (fs.requestId + 1) % 65536; return fs.requestId }

    func sendProtobuf(type: Int = Msg.protobufRequest.rawValue, requestId: Int? = nil, smart: Data) {
        let id = requestId ?? nextRequestId()
        // Requests here are tiny, so no outbound chunking.
        sendGfdi(gfdi(type) { w in w.u16(id); w.u32(0); w.u32(smart.count); w.u32(smart.count); w.d.append(smart) })
    }

    func fileSync(_ inner: PBW) -> Data { var smart = PBW(); smart.bytes(43, inner.d); return smart.d }

    func requestFileList(cursor: UInt64? = nil, startPage: UInt64? = nil) {
        fs.listing = true
        var req = PBW()
        if let c = cursor { req.uint(1, c) } else if let p = startPage { req.uint(2, p) }
        if gbFlags {
            // Gadgetbridge's filter: flags1/flags2 = FileId(0xa5a5, 0xa5a5)
            var fid = PBW()
            for f in 1...2 { fid.varint(UInt64(f << 3 | 1)); withUnsafeBytes(of: UInt64(42405).littleEndian) { fid.d.append(contentsOf: $0) } }
            req.bytes(4, fid.d); req.bytes(5, fid.d)
        }
        req.uint(7, 0) // include_metadata: timestamps
        var svc = PBW(); svc.bytes(9, req.d)
        log("FileSync: requesting file list\(cursor.map { " (cursor \($0))" } ?? "")")
        sendProtobuf(smart: fileSync(svc))
    }

    func onProtobuf(_ type: Int, _ r0: R) {
        var r = r0
        let id = r.u16(), offset = r.u32(), total = r.u32(), len = r.u32()
        let part = r.bytes(min(len, r.remaining))
        var buf = (offset == 0 ? Data() : fs.chunks[id] ?? Data())
        buf.append(part)
        if buf.count < total {
            fs.chunks[id] = buf
            // ProtobufStatusMessage: ACK, request id, offset, KEPT, NO_ERROR
            sendGfdi(gfdi(Msg.response.rawValue) { w in w.u16(type); w.u8(0); w.u16(id); w.u32(offset); w.u8(0); w.u8(0) })
            return
        }
        fs.chunks[id] = nil
        sendGfdi(ack(type))
        let smart = pbDecode(buf)
        guard let fsvc = pbFirst(smart, 43) else {
            log("protobuf \(type == 5043 ? "request" : "response") #\(id) for Smart fields \(smart.map { $0.0 }) (\(buf.count)B); ignored")
            return
        }
        onFileSync(pbDecode(fsvc.d), requestId: id, isRequest: type == Msg.protobufRequest.rawValue)
    }

    func onFileSync(_ f: [(Int, PB)], requestId: Int, isRequest: Bool) {
        if let lr = pbFirst(f, 10) {
            let l = pbDecode(lr.d)
            let status = pbFirst(l, 1)?.u, cursor = pbFirst(l, 2)?.u, nextPage = pbFirst(l, 3)?.u
            let entries = l.filter { $0.0 == 4 }.map { parseSyncFile($0.1.d) }
            fs.files += entries
            log("FileSync list: status=\(status.map { String($0) } ?? "-") files=\(entries.count) cursor=\(cursor.map { String($0) } ?? "-") nextPage=\(nextPage.map { String($0) } ?? "-")")
            if let c = cursor, !entries.isEmpty { requestFileList(cursor: c); return }
            fs.listing = false; fs.listed = true
            printSyncListing()
            if downloadPerType > 0 {
                for (_, group) in Dictionary(grouping: fs.files.filter(isWanted), by: { $0.kind }) {
                    fs.queue += group.sorted { $0.timestamp > $1.timestamp }.prefix(downloadPerType)
                }
                log("queued \(fs.queue.count) files (newest \(downloadPerType) per kind)")
            }
            newSyncNext()
        } else if let fr = pbFirst(f, 2) {
            let x = pbDecode(fr.d)
            let status = Int(pbFirst(x, 1)?.u ?? 99), handle = Int(pbFirst(x, 3)?.u ?? 0)
            log("FileSync file response: status=\(status) handle=\(handle) raw=\(hex(fr.d))")
            guard status == 0 else { newSyncNext(); return }
            if fs.pendingFileHandle == handle {
                log("  (repeat for file handle \(handle) while transfer in progress: \(fs.packets) packets, \(fs.transferBuf.count) B so far; not reopening)")
                return
            }
            fs.pendingFileHandle = handle
            var w = W(); w.u8(0); w.u8(Int(Req.registerReq.rawValue)); w.u64(clientID); w.u16(0x2018); w.u8(plainTransfer ? 0 : 2)
            rawWrite(w.d)
        } else if pbFirst(f, 5) != nil {
            log("FileSync transfer status request; answering unk1=1 (as Gadgetbridge does)")
            var resp = PBW(); resp.uint(1, 1)
            var svc = PBW(); svc.bytes(21, resp.d)
            sendProtobuf(type: Msg.protobufResponse.rawValue, requestId: requestId, smart: fileSync(svc))
        } else {
            log("FileSync message fields \(f.map { $0.0 }) (\(isRequest ? "from watch" : "response")); no action")
        }
    }

    func parseSyncFile(_ d: Data) -> SyncFile {
        let f = pbDecode(d)
        let id = pbDecode(pbFirst(f, 1)?.d ?? Data())
        let t = pbDecode(pbFirst(f, 2)?.d ?? Data())
        let nameId = pbFirst(t, 3).map { Int($0.u) }
        var name = pbFirst(t, 2).map { String(decoding: $0.d, as: UTF8.self) }
        if let n = name, let i = nameId { fs.nameById[i] = n }
        if name == nil, let i = nameId { name = fs.nameById[i] }
        return SyncFile(raw: d, id1: pbFirst(id, 1)?.u ?? 0, id2: pbFirst(id, 2)?.u ?? 0,
                        typeCode: pbFirst(t, 1).map { Int($0.u) }, typeName: name, nameId: nameId,
                        size: pbFirst(f, 3)?.u ?? 0, pageId: pbFirst(f, 5).map { Int($0.u) },
                        timestamp: UInt32(truncatingIfNeeded: pbFirst(f, 7)?.u ?? 0))
    }

    func isWanted(_ f: SyncFile) -> Bool {
        let k = f.kind.lowercased()
        return f.size > 0 && wantedKinds.contains(k)
    }

    func printSyncListing() {
        let df = ISO8601DateFormatter()
        print("\nFILESYNC LISTING: \(fs.files.count) files")
        var lines: [String] = []
        for (kind, group) in Dictionary(grouping: fs.files, by: { $0.kind }).sorted(by: { $0.key < $1.key }) {
            let newest = group.max { $0.timestamp < $1.timestamp }
            let total = group.reduce(0) { $0 + $1.size }
            print("\n\(kind): \(group.count) files, \(total) B total, newest \(newest?.date.map { df.string(from: $0) } ?? "-")")
            for f in group.sorted(by: { $0.timestamp > $1.timestamp }) {
                let line = String(format: "  %016llx-%016llx  code=%@ nameId=%@ size=%llu page=%@ %@",
                                  f.id1, f.id2, f.typeCode.map { String($0) } ?? "-", f.nameId.map { String($0) } ?? "-",
                                  f.size, f.pageId.map { String($0) } ?? "-", f.date.map { df.string(from: $0) } ?? "-")
                print(line)
                lines.append("\(kind)\t\(line.trimmingCharacters(in: .whitespaces))")
            }
        }
        print("")
        fflush(stdout)
        try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
        try? lines.joined(separator: "\n").write(toFile: (outDir as NSString).appendingPathComponent("filesync-listing.txt"), atomically: true, encoding: .utf8)
    }

    func newSyncNext() {
        fs.current = nil
        if !fs.listed {
            if !fs.listing { requestFileList() }
            return
        }
        guard !fs.queue.isEmpty else {
            log("done; no files marked synced, archived or deleted on the watch")
            finish(code: 0); return
        }
        let f = fs.queue.removeFirst()
        fs.current = f
        log("requesting \(f.kind) \(String(format: "%016llx", f.id2)) size=\(f.size)")
        var req = PBW(); req.bytes(1, f.raw); req.uint(2, 24); req.uint(3, 0); req.uint(4, 0); req.uint(5, 15)
        var svc = PBW(); svc.bytes(1, req.d)
        sendProtobuf(smart: fileSync(svc))
    }

    // File bytes come on their own ML handle (service 0x2018), not via GFDI.
    func transferOpened(handle: Int, reliable: Int) {
        fs.transferHandle = handle; fs.transferStarted = false; fs.transferBuf = Data()
        fs.mlr = reliable != 0; fs.nextRcv = 0; fs.lastSentAck = 0; fs.reqAcked = false; fs.packets = 0
        guard let fh = fs.pendingFileHandle else { return }
        var body = W(); body.u8(0); body.u8(0); body.u16(fh); body.u8(0); body.u8(0)
        log("transfer channel open on handle \(handle) (reliable=\(reliable)); requesting file handle \(fh)")
        if fs.mlr { mlrSendRequest(body.d, attempt: 1) } else { rawWrite(Data([UInt8(handle)]) + body.d) }
    }

    // Minimal MLR (Gadgetbridge MlrCommunicator): byte0 = 0x80 | (handle&7)<<4 | reqNum>>2,
    // byte1 = (reqNum&3)<<6 | seqNum. reqNum acks the peer's packets; we send one data packet (seq 0).
    func mlrPacket(req: Int, seq: Int, _ data: Data) -> Data {
        let h = (fs.transferHandle ?? 0) & 0x07
        return Data([UInt8(0x80 | h << 4 | (req >> 2) & 0x0F), UInt8((req & 0x03) << 6 | seq & 0x3F)]) + data
    }

    func mlrSendRequest(_ body: Data, attempt: Int) {
        guard fs.transferHandle != nil, !fs.reqAcked else { return }
        if attempt > 1 { log("MLR: re-sending file request (attempt \(attempt))") }
        rawWrite(mlrPacket(req: fs.nextRcv, seq: 0, body))
        guard attempt < 6 else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + Double(attempt)) { self.mlrSendRequest(body, attempt: attempt + 1) }
    }

    func mlrReceive(_ p: Data) {
        guard p.count >= 2 else { return }
        let b0 = Int(p[p.startIndex]), b1 = Int(p[p.startIndex + 1])
        let reqNum = (b0 & 0x0F) << 2 | (b1 >> 6) & 0x03, seq = b1 & 0x3F
        if reqNum != 0 { fs.reqAcked = true }
        guard p.count > 2 else { return }
        if seq == fs.nextRcv {
            fs.nextRcv = (fs.nextRcv + 1) % 64
            fs.packets += 1
            transferData(p.dropFirst(2))
            let unacked = (fs.nextRcv - fs.lastSentAck + 64) % 64
            if unacked >= 5 { mlrAck() } else {
                let expect = fs.nextRcv
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { if fs.nextRcv == expect && fs.lastSentAck != expect { self.mlrAck() } }
            }
            if fs.packets % 100 == 0 { log("MLR: \(fs.packets) packets, \(fs.transferBuf.count) B") }
        } else {
            mlrAck() // out of order: re-ack what we have so the watch retransmits
        }
    }

    func mlrAck() {
        guard fs.transferHandle != nil else { return }
        rawWrite(mlrPacket(req: fs.nextRcv, seq: 0, Data()))
        fs.lastSentAck = fs.nextRcv
    }

    func transferData(_ d: Data) {
        if !fs.transferStarted {
            fs.transferStarted = true
            if d.count >= 3, d.prefix(3) == Data([0, 0, 0]) {
                log("transfer start header \(hex(d))")
                return
            }
            log("no start header; first packet \(hex(d.prefix(24))) treated as data")
        }
        fs.transferBuf.append(d)
    }

    func transferClosed() {
        let raw = fs.transferBuf
        fs.transferHandle = nil; fs.pendingFileHandle = nil
        guard let f = fs.current else { newSyncNext(); return }
        log("transfer closed with \(raw.count) compressed bytes")
        guard !raw.isEmpty else { log("empty transfer; skipping save"); newSyncNext(); return }
        var body = raw
        // zlib header (the watch uses a 2 KB window: 38 cb); Apple's .zlib wants raw deflate.
        if body.count > 2 {
            let cmf = Int(body[body.startIndex]), flg = Int(body[body.startIndex + 1])
            if cmf & 0x0F == 8, (cmf << 8 | flg) % 31 == 0 { body = body.dropFirst(2) }
        }
        let inflated = (try? (body as NSData).decompressed(using: .zlib)) as Data?
        let data = inflated ?? raw
        let isFit = data.count > 12 && data[data.startIndex + 8..<data.startIndex + 12] == Data(".FIT".utf8)
        let dir = (outDir as NSString).appendingPathComponent(f.kind)
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let tf = DateFormatter(); tf.dateFormat = "yyyy-MM-dd-HH-mm-ss"; tf.timeZone = TimeZone(identifier: "UTC")
        let name = "\(f.date.map { tf.string(from: $0) } ?? "nodate")_\(String(format: "%016llx", f.id2)).\(isFit ? "fit" : (inflated == nil ? "deflate" : "bin"))"
        let path = (dir as NSString).appendingPathComponent(name)
        do {
            try data.write(to: URL(fileURLWithPath: path))
            if let dt = f.date { try? FileManager.default.setAttributes([.modificationDate: dt], ofItemAtPath: path) }
            savedFiles.append(path)
            log("saved \(f.kind)/\(name) (\(data.count) B\(inflated == nil ? ", could not inflate" : ""), FIT=\(isFit))")
        } catch { log("save failed: \(error)") }
        newSyncNext()
    }
}

// Top-level globals above must be initialized before the run loop starts.
let client = Client()
RunLoop.main.run()
