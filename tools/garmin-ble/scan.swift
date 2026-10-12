// Read-only BLE scan for Garmin watches. Never connects or pairs.
import Foundation
import CoreBluetooth

let garminML = CBUUID(string: "6A4E2800-667B-11E3-949A-0800200C9A66")
let seconds = Double(CommandLine.arguments.dropFirst().first ?? "30") ?? 30

final class Scanner: NSObject, CBCentralManagerDelegate {
    var central: CBCentralManager!
    var seen: [UUID: String] = [:]
    var allCount = 0

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
    }

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        print("state:", c.state.rawValue, "(5 = poweredOn)", "authorization:", CBManager.authorization.rawValue, "(3 = allowed)")
        guard c.state == .poweredOn else { return }
        let connected = c.retrieveConnectedPeripherals(withServices: [garminML])
        print("retrieveConnectedPeripherals(ML service):", connected.isEmpty ? "none" : connected.map { "\($0.name ?? "?") \($0.identifier)" }.joined(separator: ", "))
        c.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
        print("scanning \(Int(seconds))s ...")
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) {
            c.stopScan()
            print("done. peripherals seen: \(self.allCount), garmin-like: \(self.seen.count)")
            exit(0)
        }
    }

    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral, advertisementData ad: [String: Any], rssi: NSNumber) {
        allCount += 1
        let name = (ad[CBAdvertisementDataLocalNameKey] as? String) ?? p.name ?? ""
        let services = (ad[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? []) + (ad[CBAdvertisementDataOverflowServiceUUIDsKey] as? [CBUUID] ?? [])
        let mfg = (ad[CBAdvertisementDataManufacturerDataKey] as? Data) ?? Data()
        // Garmin Bluetooth company id 0x0087 (little-endian 87 00)
        let garminMfg = mfg.count >= 2 && mfg[0] == 0x87 && mfg[1] == 0x00
        let looksGarmin = garminMfg || services.contains(garminML) || name.lowercased().contains("fenix") || name.lowercased().contains("garmin")
        guard looksGarmin, seen[p.identifier] == nil else { return }
        seen[p.identifier] = name
        print("---")
        print("name:", name.isEmpty ? "(none)" : name, " id:", p.identifier, " rssi:", rssi)
        print("services:", services.map { $0.uuidString })
        print("manufacturerData:", mfg.map { String(format: "%02x", $0) }.joined())
        print("connectable:", ad[CBAdvertisementDataIsConnectable] ?? "?")
    }
}

let s = Scanner()
RunLoop.main.run()
