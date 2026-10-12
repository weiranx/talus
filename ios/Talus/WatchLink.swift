import CoreBluetooth
import UIKit

/// A read-only Bluetooth session with a Garmin watch: connect, list its files over protobuf
/// FileSync, and download them over the MLR file channel. Never deletes, archives or marks files,
/// never answers the watch's auth negotiation, and never sets its clock.
/// Ported from tools/garmin-ble/sync.swift; NOTES.md there explains the protocol.
@MainActor
final class WatchLink: NSObject {
    enum LinkError: LocalizedError {
        case bluetoothOff, unauthorized, notFound, noGarminService, disconnected(String?), timeout(String)
        case authRequested, refused(String)

        var errorDescription: String? {
            switch self {
            case .bluetoothOff: "Bluetooth is off. Turn it on in Control Center and try again."
            case .unauthorized: "Talus doesn't have Bluetooth access. Allow it in Settings, Privacy & Security, Bluetooth."
            case .notFound: "No Garmin watch found nearby. Wake the watch, keep it close, and try again."
            case .noGarminService: "That device doesn't offer Garmin's sync service."
            case .disconnected(let why): "The watch disconnected\(why.map { ": \($0)" } ?? ".")"
            case .timeout(let step): "The watch stopped answering while \(step)."
            case .authRequested: "The watch asked to authenticate, which Talus doesn't do. Sync with Garmin Connect once, then try again."
            case .refused(let what): what
            }
        }
    }

    /// What happened, for the status line and the log.
    var onEvent: ((String) -> Void)?
    private(set) var device: GarminWire.DeviceInfo?
    /// True when the watch was already connected to the phone (usually by Garmin Connect) and
    /// Talus shared that link instead of finding it by scanning.
    private(set) var sharedLink = false

    private var central: CBCentralManager?
    private var peripheral: CBPeripheral?
    private var sendChar: CBCharacteristic?
    private var recvChar: CBCharacteristic?
    private var gfdiHandle: Int?
    private var decoder = GarminWire.CobsDecoder()
    private var writeQueue: [Data] = []
    private var writing = false

    private var connectWaiter: CheckedContinuation<GarminWire.DeviceInfo, Error>?
    private var poweredWaiter: CheckedContinuation<Void, Error>?
    private var listWaiter: CheckedContinuation<[WatchFile], Error>?
    private var downloadWaiter: CheckedContinuation<Data, Error>?
    private var watchdog: Task<Void, Never>?

    private var requestId = 0
    private var protoChunks: [Int: Data] = [:]
    private var names: [Int: String] = [:]
    private var listed: [WatchFile] = []

    // The file being downloaded
    private var awaitingFileResponse = false
    private var fileSize = 0
    private var pendingFileHandle: Int?
    private var transferHandle: Int?
    private var transferStarted = false
    private var transferBuf = Data()
    private var nextRcv = 0
    private var lastSentAck = 0
    private var requestAcked = false
    private var progress: ((Int) -> Void)?

    private static let savedPeripheral = "watch.peripheral"
    private let ml = CBUUID(string: GarminWire.mlService)

    // MARK: Public

    func connect() async throws -> GarminWire.DeviceInfo {
        let c = central ?? CBCentralManager(delegate: self, queue: nil)
        central = c
        if c.state != .poweredOn {
            try await withCheckedThrowingContinuation { poweredWaiter = $0; checkPower(c) }
        }
        return try await withCheckedThrowingContinuation { cont in
            connectWaiter = cont
            arm(30, "connecting")
            find(c)
        }
    }

    func listFiles() async throws -> [WatchFile] {
        listed = []; names = [:]
        return try await withCheckedThrowingContinuation { cont in
            listWaiter = cont
            arm(30, "listing files")
            requestList(cursor: nil)
        }
    }

    /// Returns the file as the watch sent it (zlib-compressed). `progress` gets bytes received.
    func download(_ f: WatchFile, progress: @escaping (Int) -> Void) async throws -> Data {
        guard downloadWaiter == nil else { throw LinkError.refused("A download is already running.") }
        self.progress = progress
        fileSize = f.size
        pendingFileHandle = nil
        awaitingFileResponse = true
        return try await withCheckedThrowingContinuation { cont in
            downloadWaiter = cont
            arm(20, "starting a download")
            sendProtobuf(GarminWire.fileRequest(f))
        }
    }

    func disconnect() {
        watchdog?.cancel()
        if let p = peripheral, let c = central { c.cancelPeripheralConnection(p) }
        peripheral = nil
        failAll(LinkError.disconnected(nil))
    }

    // MARK: Waiting

    /// Fails whatever is pending if nothing comes back in time. Any traffic re-arms it.
    private func arm(_ seconds: Double, _ step: String) {
        watchdog?.cancel()
        watchdog = Task { [weak self] in
            try? await Task.sleep(for: .seconds(seconds))
            guard !Task.isCancelled, let self else { return }
            self.failAll(LinkError.timeout(step))
        }
    }

    private var currentStep = "connecting"
    private func keepAlive(_ seconds: Double = 20) { arm(seconds, currentStep) }

    private func failAll(_ e: Error) {
        watchdog?.cancel()
        poweredWaiter?.resume(throwing: e); poweredWaiter = nil
        connectWaiter?.resume(throwing: e); connectWaiter = nil
        listWaiter?.resume(throwing: e); listWaiter = nil
        downloadWaiter?.resume(throwing: e); downloadWaiter = nil
    }

    private func event(_ s: String) { onEvent?(s) }

    // MARK: Finding the watch

    private func checkPower(_ c: CBCentralManager) {
        switch c.state {
        case .poweredOn: poweredWaiter?.resume(); poweredWaiter = nil
        case .unauthorized: poweredWaiter?.resume(throwing: LinkError.unauthorized); poweredWaiter = nil
        case .poweredOff, .unsupported: poweredWaiter?.resume(throwing: LinkError.bluetoothOff); poweredWaiter = nil
        default: break // unknown or resetting: wait for the next update
        }
    }

    private func find(_ c: CBCentralManager) {
        currentStep = "connecting"
        // Garmin Connect usually holds the link already; share it rather than scanning.
        let connected = c.retrieveConnectedPeripherals(withServices: [ml])
        event("Already connected to the phone: \(connected.isEmpty ? "none" : connected.map { $0.name ?? "unnamed" }.joined(separator: ", "))")
        let saved = UserDefaults.standard.string(forKey: Self.savedPeripheral).flatMap(UUID.init)
        if let p = connected.first(where: { $0.identifier == saved }) ?? connected.first {
            sharedLink = true
            open(p)
            return
        }
        sharedLink = false
        event("Scanning for the watch")
        c.scanForPeripherals(withServices: nil, options: nil)
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(15))
            guard let self, self.peripheral == nil, self.connectWaiter != nil else { return }
            c.stopScan()
            self.failAll(LinkError.notFound)
        }
    }

    private func open(_ p: CBPeripheral) {
        peripheral = p
        p.delegate = self
        event("Connecting to \(p.name ?? "the watch")")
        central?.connect(p, options: nil)
    }

    // MARK: Writes

    private var writeType: CBCharacteristicWriteType {
        (sendChar?.properties.contains(.write) ?? false) ? .withResponse : .withoutResponse
    }

    private func rawWrite(_ d: Data) { writeQueue.append(d); pump() }

    private func pump() {
        guard !writing, !writeQueue.isEmpty, let p = peripheral, let ch = sendChar else { return }
        let d = writeQueue.removeFirst()
        if writeType == .withResponse {
            writing = true
            p.writeValue(d, for: ch, type: .withResponse)
        } else {
            p.writeValue(d, for: ch, type: .withoutResponse)
            Task { [weak self] in self?.pump() }
        }
    }

    private func sendGfdi(_ msg: Data) {
        guard let h = gfdiHandle, let p = peripheral else { return }
        let enc = GarminWire.cobsEncode(msg)
        let chunk = max(19, p.maximumWriteValueLength(for: writeType) - 1)
        var i = 0
        while i < enc.count {
            rawWrite(Data([UInt8(h)]) + enc[i..<min(i + chunk, enc.count)])
            i += chunk
        }
    }

    private func nextRequestId() -> Int { requestId = (requestId + 1) % 65536; return requestId }

    private func sendProtobuf(_ payload: Data, type: Int = GarminWire.Msg.protobufRequest.rawValue, requestId: Int? = nil) {
        sendGfdi(GarminWire.protobuf(type: type, requestId: requestId ?? nextRequestId(), payload))
    }

    private func requestList(cursor: UInt64?) {
        currentStep = "listing files"
        sendProtobuf(GarminWire.fileListRequest(cursor: cursor))
    }

    // MARK: Inbound

    private func received(_ v: Data) {
        guard let first = v.first else { return }
        let handle = Int(first)
        if handle & 0x80 != 0 {
            if let th = transferHandle, GarminWire.mlrHeader(v)?.handle == th & 0x07 { mlrReceive(v) }
            return
        }
        if handle == 0 { management(v.dropFirst()); return }
        guard handle == gfdiHandle else { return }
        for frame in decoder.feed(v.dropFirst()) { gfdi(frame) }
    }

    private func management(_ d: Data) {
        guard let m = GarminWire.management(d) else { return }
        switch m {
        case .closeAll:
            rawWrite(GarminWire.register(service: GarminWire.gfdiService, reliable: 0))
        case .registered(let svc, let status, let h, let reliable):
            guard status == 0 else {
                failAll(LinkError.refused("The watch refused Talus's request (service \(svc), status \(status)). Try again."))
                return
            }
            event("Watch opened service 0x\(String(svc, radix: 16)) on handle \(h)\(reliable != 0 ? " (reliable)" : "")")
            if svc == GarminWire.gfdiService { gfdiHandle = h; keepAlive() }
            if svc == GarminWire.fileService { transferOpened(handle: h, reliable: reliable) }
        case .closed(let svc, _):
            if svc == GarminWire.fileService { transferClosed() }
        case .other:
            break
        }
    }

    private func gfdi(_ frame: Data) {
        guard let (type, body) = GarminWire.parseGfdi(frame) else { return }
        typealias M = GarminWire.Msg
        switch M(rawValue: type) {
        case .deviceInformation:
            guard let info = GarminWire.deviceInfo(body) else { return }
            device = info
            sendGfdi(GarminWire.deviceInfoReply(protocolVersion: info.protocolVersion, hostName: UIDevice.current.name))
        case .configuration:
            var r = body
            let n = r.u8()
            sendGfdi(GarminWire.ack(type))
            sendGfdi(GarminWire.configuration(length: n))
            Task { [weak self] in
                try? await Task.sleep(for: .seconds(1))
                guard let self, let info = self.device else { return }
                self.watchdog?.cancel()
                self.connectWaiter?.resume(returning: info); self.connectWaiter = nil
            }
        case .protobufRequest, .protobufResponse:
            protobuf(type, body)
        case .authNegotiation:
            failAll(LinkError.authRequested)
        case .currentTimeRequest:
            sendGfdi(GarminWire.ack(type, status: 2)) // UNSUPPORTED: Talus never sets the watch's clock
        case .response, .fileTransferData, .downloadRequest:
            break
        case .none:
            sendGfdi(GarminWire.ack(type))
        }
    }

    private func protobuf(_ type: Int, _ r0: GarminWire.Reader) {
        var r = r0
        let id = r.u16(), offset = r.u32(), total = r.u32(), len = r.u32()
        var buf = offset == 0 ? Data() : protoChunks[id] ?? Data()
        buf.append(r.bytes(len))
        keepAlive()
        if buf.count < total {
            protoChunks[id] = buf
            sendGfdi(GarminWire.protobufChunkAck(type: type, requestId: id, offset: offset))
            return
        }
        protoChunks[id] = nil
        sendGfdi(GarminWire.ack(type))
        guard let msg = GarminWire.fileSyncMessage(buf, names: &names) else { return }
        switch msg {
        case .list(let files, let cursor):
            listed += files
            event("Listed \(listed.count) files on the watch")
            if let cursor, !files.isEmpty { requestList(cursor: cursor); return }
            watchdog?.cancel()
            listWaiter?.resume(returning: listed); listWaiter = nil
        case .fileResponse(let status, let handle):
            // The watch repeats this every ~5 s while a transfer runs; reopening restarts it empty.
            guard awaitingFileResponse, pendingFileHandle == nil else { return }
            awaitingFileResponse = false
            guard status == 0 else {
                watchdog?.cancel()
                downloadWaiter?.resume(throwing: LinkError.refused("The watch wouldn't send that file (status \(status)).")); downloadWaiter = nil
                return
            }
            pendingFileHandle = handle
            currentStep = "opening the file channel"
            rawWrite(GarminWire.register(service: GarminWire.fileService, reliable: 2))
        case .transferStatusRequest:
            sendProtobuf(GarminWire.transferStatusResponse(), type: GarminWire.Msg.protobufResponse.rawValue, requestId: id)
        case .other:
            break
        }
    }

    // MARK: File channel (MLR)

    private func transferOpened(handle: Int, reliable: Int) {
        transferHandle = handle; transferStarted = false; transferBuf = Data()
        nextRcv = 0; lastSentAck = 0; requestAcked = false
        guard let fh = pendingFileHandle else { return }
        currentStep = "downloading"
        keepAlive()
        sendFileRequest(GarminWire.fileHandleRequest(fh), attempt: 1)
    }

    private func sendFileRequest(_ body: Data, attempt: Int) {
        guard let h = transferHandle, !requestAcked else { return }
        rawWrite(GarminWire.mlrPacket(handle: h, req: nextRcv, seq: 0, body))
        guard attempt < 6 else { return }
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(Double(attempt)))
            self?.sendFileRequest(body, attempt: attempt + 1)
        }
    }

    private func mlrReceive(_ p: Data) {
        guard let hdr = GarminWire.mlrHeader(p) else { return }
        if hdr.req != 0 { requestAcked = true }
        guard p.count > 2 else { return }
        keepAlive()
        if hdr.seq == nextRcv {
            nextRcv = (nextRcv + 1) % 64
            payload(p.dropFirst(2))
            if (nextRcv - lastSentAck + 64) % 64 >= 5 {
                mlrAck()
            } else {
                let expect = nextRcv
                Task { [weak self] in
                    try? await Task.sleep(for: .milliseconds(250))
                    guard let self, self.nextRcv == expect, self.lastSentAck != expect else { return }
                    self.mlrAck()
                }
            }
        } else {
            mlrAck() // out of order: re-ack what we have so the watch resends
        }
    }

    private func mlrAck() {
        guard let h = transferHandle else { return }
        rawWrite(GarminWire.mlrPacket(handle: h, req: nextRcv, seq: 0))
        lastSentAck = nextRcv
    }

    private func payload(_ d: Data) {
        if !transferStarted {
            transferStarted = true
            if d.count >= 3, d.prefix(3) == Data([0, 0, 0]) { return } // start header
        }
        transferBuf.append(d)
        progress?(transferBuf.count)
    }

    private func transferClosed() {
        let raw = transferBuf
        transferHandle = nil; pendingFileHandle = nil; transferBuf = Data(); progress = nil
        watchdog?.cancel()
        guard let w = downloadWaiter else { return }
        downloadWaiter = nil
        if raw.isEmpty { w.resume(throwing: LinkError.refused("The watch closed the file without sending it.")) } else { w.resume(returning: raw) }
    }
}

// MARK: - CoreBluetooth

extension WatchLink: @MainActor CBCentralManagerDelegate, @MainActor CBPeripheralDelegate {
    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        checkPower(c)
    }

    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral, advertisementData ad: [String: Any], rssi: NSNumber) {
        let svcs = (ad[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID]) ?? []
        let mfg = (ad[CBAdvertisementDataManufacturerDataKey] as? Data) ?? Data()
        let connectable = (ad[CBAdvertisementDataIsConnectable] as? NSNumber)?.boolValue ?? true
        // Garmin watches advertise without a name; the manufacturer data says Garmin (0x0087).
        let garmin = svcs.contains(ml) || (mfg.count >= 2 && Int(mfg[mfg.startIndex]) | Int(mfg[mfg.startIndex + 1]) << 8 == GarminWire.garminCompany)
        guard garmin, connectable, peripheral == nil else { return }
        c.stopScan()
        open(p)
    }

    func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
        event("Connected")
        keepAlive()
        p.discoverServices([ml])
    }

    func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        let why = error?.localizedDescription
        peripheral = nil
        failAll(LinkError.disconnected(why))
    }

    func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
        let why = error?.localizedDescription
        peripheral = nil; gfdiHandle = nil; transferHandle = nil; writeQueue = []; writing = false
        failAll(LinkError.disconnected(why))
    }

    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        guard let svc = p.services?.first(where: { $0.uuid == ml }) else {
            failAll(LinkError.noGarminService); return
        }
        UserDefaults.standard.set(p.identifier.uuidString, forKey: Self.savedPeripheral)
        p.discoverCharacteristics(nil, for: svc)
    }

    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        let chars = s.characteristics ?? []
        for i in 0x2810...0x2814 {
            let r = CBUUID(string: GarminWire.mlUUID(i)), w = CBUUID(string: GarminWire.mlUUID(i + 0x10))
            if let rc = chars.first(where: { $0.uuid == r }), let wc = chars.first(where: { $0.uuid == w }) {
                recvChar = rc; sendChar = wc
                p.setNotifyValue(true, for: rc)
                return
            }
        }
        failAll(LinkError.noGarminService)
    }

    func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor ch: CBCharacteristic, error: Error?) {
        let why = error?.localizedDescription
        if let why { failAll(LinkError.disconnected(why)); return }
        event("Opening Garmin's sync service")
        rawWrite(GarminWire.closeAll())
    }

    func peripheral(_ p: CBPeripheral, didWriteValueFor ch: CBCharacteristic, error: Error?) {
        writing = false
        pump()
    }

    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        let v = ch.value
        guard let v, !v.isEmpty else { return }
        received(v)
    }
}
