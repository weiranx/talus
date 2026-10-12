import Foundation
import Testing
@testable import Talus

struct GarminWireTests {
    private func hex(_ s: String) -> Data {
        var d = Data(); var i = s.startIndex
        while i < s.endIndex { let j = s.index(i, offsetBy: 2); d.append(UInt8(s[i..<j], radix: 16)!); i = j }
        return d
    }

    @Test func managementBytesMatchTheMacClient() {
        #expect(GarminWire.closeAll() == hex("000502000000000000000000"))
        #expect(GarminWire.register(service: 0x0001, reliable: 0) == hex("00000200000000000000010000"))
        #expect(GarminWire.register(service: 0x2018, reliable: 2) == hex("00000200000000000000182002"))
        #expect(GarminWire.management(hex("01020000000000000018200081" + "01")) ==
                .registered(service: 0x2018, status: 0, handle: 0x81, reliable: 1))
        #expect(GarminWire.management(hex("030200000000000000182081")) == .closed(service: 0x2018, handle: 0x81))
        #expect(GarminWire.management(hex("060700000000000000")) == nil) // another client
    }

    @Test func gfdiSurvivesCobsAndChunking() {
        let msg = GarminWire.protobuf(requestId: 7, GarminWire.fileListRequest(cursor: 1))
        let enc = GarminWire.cobsEncode(msg)
        #expect(!enc.dropFirst().dropLast().contains(0))
        var dec = GarminWire.CobsDecoder()
        var frames: [Data] = []
        var i = 0
        while i < enc.count { frames += dec.feed(enc[i..<min(i + 5, enc.count)]); i += 5 }
        #expect(frames == [msg])
        let parsed = GarminWire.parseGfdi(frames[0])
        #expect(parsed?.type == 5043)
        var corrupt = msg; corrupt[6] ^= 0xFF
        #expect(GarminWire.parseGfdi(corrupt) == nil)
    }

    @Test func compactTypesExpand() {
        let f = GarminWire.gfdi(0x8000 | 24)
        #expect(GarminWire.parseGfdi(f)?.type == 5024)
    }

    @Test func mlrHeaderRoundTrips() {
        let p = GarminWire.mlrPacket(handle: 0x81, req: 45, seq: 33, Data([9]))
        let h = GarminWire.mlrHeader(p)
        #expect(h?.handle == 1 && h?.req == 45 && h?.seq == 33)
        #expect(p[0] & 0x80 != 0)
        #expect(GarminWire.mlrHeader(Data([0x01, 0x00])) == nil)
    }

    @Test func listingParsesAndNamesCarryOver() {
        func file(_ id2: UInt64, name: String?, nameId: UInt64, size: UInt64, ts: UInt64) -> Data {
            var id = ProtoWriter(); id.uint(1, 0x452eea72_000000c7); id.uint(2, id2)
            var t = ProtoWriter(); t.uint(1, 0); if let name { t.bytes(2, Data(name.utf8)) }; t.uint(3, nameId)
            var f = ProtoWriter(); f.bytes(1, id.d); f.bytes(2, t.d); f.uint(3, size); f.uint(5, 8645); f.uint(7, ts)
            return f.d
        }
        var list = ProtoWriter(); list.uint(1, 0); list.uint(2, 1)
        list.bytes(4, file(0x0100000000_06eff5, name: "FIT_TYPE_4", nameId: 1, size: 454645, ts: 1_160_702_578))
        list.bytes(4, file(0x0100000000_07b1c0, name: nil, nameId: 1, size: 504256, ts: 1_160_531_348))
        var svc = ProtoWriter(); svc.bytes(10, list.d)
        var names: [Int: String] = [:]
        guard case .list(let files, let cursor)? = GarminWire.fileSyncMessage(GarminWire.fileSync(svc), names: &names) else {
            Issue.record("not a list"); return
        }
        #expect(cursor == 1)
        #expect(files.map(\.kind) == ["FIT_TYPE_4", "FIT_TYPE_4"])
        #expect(files[0].size == 454645)
        #expect(files[0].key == "FIT_TYPE_4-452eea72000000c7-010000000006eff5")
        #expect(files[0].fileName == "FIT_TYPE_4_2026-10-12-01-22-58_010000000006eff5.fit")
        // The request echoes the File message back untouched.
        #expect(Proto.first(Proto.decode(Proto.first(Proto.decode(Proto.first(Proto.decode(GarminWire.fileRequest(files[1])), 43)!.data), 1)!.data), 1)?.data == files[1].raw)
    }

    @Test func fileResponseDecodes() {
        var svc = ProtoWriter(); svc.bytes(2, hex("0800100018b309380b"))
        var names: [Int: String] = [:]
        guard case .fileResponse(let status, let handle)? = GarminWire.fileSyncMessage(GarminWire.fileSync(svc), names: &names) else {
            Issue.record("not a file response"); return
        }
        #expect(status == 0 && handle == 1203)
    }

    /// A zlib stream with the watch's 2 KB window (header 38 cb), made with Python's zlib.
    @Test func inflatesTheWatchsZlib() throws {
        let z = hex("38cbe313b81bc4cac0c0a0e7e619c2c0c8c4ccc2cac6cec1c9c5cdc3cbc72f2028242c222a262e2129252d232b27afa0a8a4aca2aaa6aea1a9a5ada3aba76f6068646c626a666e6169656d636b673fd4f50300eef021e4")
        let out = try GarminWire.inflate(z, expectedSize: 268)
        #expect(out.count == 268)
        #expect(GarminWire.isFit(out))
        var bad = z; bad[bad.count - 1] ^= 1
        #expect(throws: GarminWire.InflateError.self) { try GarminWire.inflate(bad, expectedSize: 268) }
        #expect(throws: GarminWire.InflateError.self) { try GarminWire.inflate(Data(repeating: 1, count: 20), expectedSize: 10) }
    }
}

struct WatchSelectionTests {
    private func file(_ kind: String, _ id2: UInt64, daysAgo: Double, now: Date) -> WatchFile {
        let ts = UInt32(now.addingTimeInterval(-daysAgo * 86400).timeIntervalSince(GarminWire.garminEpoch))
        return WatchFile(raw: Data(), id1: 1, id2: id2, typeName: kind, typeCode: 0, size: 100, timestamp: ts)
    }

    @Test func firstSyncTakesTwoWeeksOfActivitiesAndAllHealth() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let files = [file("FIT_TYPE_4", 1, daysAgo: 3, now: now), file("FIT_TYPE_4", 2, daysAgo: 20, now: now),
                     file("FIT_TYPE_49", 3, daysAgo: 40, now: now), file("FIT_TYPE_3", 4, daysAgo: 1, now: now),
                     file("FIT_TYPE_4", 5, daysAgo: 1, now: now)]
        let picked = WatchSync.select(files, imported: [files[4].key], lastSync: nil, now: now)
        #expect(picked.map(\.id2) == [1, 3])
    }

    @Test func laterSyncsReachTwoWeeksOrTwoDaysBeforeTheLastOne() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let files = [file("FIT_TYPE_4", 1, daysAgo: 30, now: now), file("FIT_TYPE_4", 2, daysAgo: 25, now: now),
                     file("FIT_TYPE_4", 3, daysAgo: 10, now: now), file("FIT_TYPE_4", 4, daysAgo: 1, now: now)]
        // Last synced 26 days ago: reach back to two days before that.
        #expect(WatchSync.select(files, imported: [], lastSync: now.addingTimeInterval(-26 * 86400), now: now).map(\.id2) == [4, 3, 2])
        // Last synced an hour ago, after an interrupted first sync: still the whole two weeks.
        #expect(WatchSync.select(files, imported: [files[3].key], lastSync: now.addingTimeInterval(-3600), now: now).map(\.id2) == [3])
    }
}
