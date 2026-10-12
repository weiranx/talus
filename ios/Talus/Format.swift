import SwiftUI

// Same wording and colors as the web app (web/lib/format.ts, web/app/globals.css).

enum Fmt {
    static func n0(_ v: Double?) -> String {
        guard let v else { return "–" }
        return Int(v.rounded()).formatted(.number)
    }

    static func mi(_ v: Double) -> String { String(format: "%.1f", v) }

    static func pct(_ v: Double) -> String { "\(Int((v * 100).rounded()))%" }

    static func duration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        return String(format: "%d:%02d", s / 3600, (s % 3600) / 60)
    }

    /// Distance and vert together, the way every Talus screen shows them.
    static func distVert(mi d: Double, gain: Double, loss: Double) -> String {
        "\(mi(d)) mi · \(n0(gain)) ft up · \(n0(loss)) ft down"
    }

    private static let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    static func date(_ iso: String) -> Date? { isoDay.date(from: String(iso.prefix(10))) }

    static func day(_ iso: String, _ style: Date.FormatStyle = .dateTime.weekday(.abbreviated)) -> String {
        date(iso).map { $0.formatted(style) } ?? iso
    }

    static func capitalized(_ s: String) -> String { s.prefix(1).uppercased() + s.dropFirst() }

    static func state(_ s: String) -> String {
        s == "overreached" ? "Overreached" : s == "loaded" ? "Loaded" : "Fresh"
    }
}

enum Tone {
    case good, warn, bad, neutral

    var ink: Color {
        switch self {
        case .good: Color(hex: 0x14573A, dark: 0x8FD3AE)
        case .warn: Color(hex: 0x7A4300, dark: 0xF2C27F)
        case .bad: Color(hex: 0x8A1F17, dark: 0xF2A39C)
        case .neutral: .secondary
        }
    }

    var fill: Color {
        switch self {
        case .good: Color(hex: 0xDCEFE4, dark: 0x183A2A)
        case .warn: Color(hex: 0xFCEBD3, dark: 0x4A3112)
        case .bad: Color(hex: 0xF9DCD9, dark: 0x4D1F1B)
        case .neutral: Color.secondary.opacity(0.12)
        }
    }

    static func state(_ s: String) -> Tone { s == "overreached" ? .bad : s == "loaded" ? .warn : .good }
    static func call(_ c: String) -> Tone { c == "rest" ? .bad : c == "modify" ? .warn : .good }
}

enum Ledger {
    static func label(_ name: String) -> String { Fmt.capitalized(name) }

    static func color(_ name: String) -> Color {
        switch name {
        case "cardio": Color(hex: 0x2459C4, dark: 0x6F9BF0)
        case "climb": Color(hex: 0xC96A00, dark: 0xF0A04B)
        case "descent": Color(hex: 0x6E3C9E, dark: 0xB78BE0)
        case "distance": Color(hex: 0x1F7A6C, dark: 0x5CC4B2)
        default: .gray
        }
    }

    static func unit(_ name: String) -> String? {
        switch name {
        case "cardio": "Cardio load: 100 is one hour at your threshold heart rate."
        case "climb": "Climb load: 1 is one foot gained on a moderate grade. Steeper feet count more."
        case "descent": "Descent load: 1 is one foot lost on a moderate grade. Steeper feet count more."
        case "distance": "Distance: miles covered, flat or steep. Climb and descent carry the vert."
        default: nil
        }
    }

    /// Distance reads in miles to a tenth, the other loads as whole numbers.
    static func value(_ name: String, _ v: Double?) -> String {
        guard let v else { return "–" }
        return name == "distance" ? "\(Fmt.mi(v)) mi" : Fmt.n0(v)
    }

    /// Mirrors ledgerBadge in web/lib/format.ts.
    static func badge(vsNormal: Double?, flags: [Flag], building: Bool?) -> (String, Tone) {
        if building == true { return ("Building your normal", .neutral) }
        if let flag = flags.first(where: { $0.key == "overreach" || $0.key == "ramp" }) ?? flags.first {
            return (flag.text, flag.key == "overreach" ? .bad : .warn)
        }
        guard let v = vsNormal else { return ("No history", .neutral) }
        if v > 0.15 { return ("Loaded", .warn) }
        if v > 0.05 { return ("Building", .good) }
        if v < -0.1 { return ("Recovering", .neutral) }
        return ("Steady", .good)
    }
}

// Zone 1 is the quietest color, the hardest zone has the most contrast: dark ink on light, near white on dark.
enum Zones {
    static func colors(_ n: Int) -> [Color] {
        n == 5
            ? zip([0xC4D2CB, 0x93A9A0, 0x68796F, 0x3C4842, 0x15211D], [0x3A4A43, 0x5A6E65, 0x84998F, 0xB3C4BC, 0xE4ECE8]).map { Color(hex: $0, dark: $1) }
            : zip([0xA9BDB5, 0x52605A, 0x15211D], [0x5A6E65, 0x8FA59B, 0xE4ECE8]).map { Color(hex: $0, dark: $1) }
    }
}

extension Color {
    // The 80% below-AeT target line.
    static let target = Color(hex: 0xA3231B, dark: 0xF07A70)

    /// nonisolated: UIKit resolves dynamic colors on whatever thread renders, so a main-actor
    /// provider closure traps (seen during a long watch sync).
    nonisolated init(hex: UInt32, dark: UInt32? = nil) {
        func rgb(_ h: UInt32) -> UIColor {
            UIColor(red: CGFloat((h >> 16) & 0xFF) / 255, green: CGFloat((h >> 8) & 0xFF) / 255,
                    blue: CGFloat(h & 0xFF) / 255, alpha: 1)
        }
        let light = rgb(hex), darkColor = rgb(dark ?? hex)
        self.init(uiColor: UIColor { $0.userInterfaceStyle == .dark ? darkColor : light })
    }
}

struct Pill: View {
    let text: String
    let tone: Tone

    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8).padding(.vertical, 3)
            .foregroundStyle(tone.ink)
            .background(tone.fill, in: Capsule())
    }
}

struct Card<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
    }
}

/// Placeholder cards shaped like a screen's content while its fetch runs.
struct SkeletonScreen: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                SkeletonBar(width: 140, height: 14)
                RoundedRectangle(cornerRadius: 16).fill(Color(hex: 0x15211D, dark: 0x22302B)).frame(height: 190)
                    .overlay(alignment: .topLeading) {
                        VStack(alignment: .leading, spacing: 12) {
                            SkeletonBar(width: 90, height: 10, onDark: true)
                            SkeletonBar(width: 220, height: 26, onDark: true)
                            SkeletonBar(height: 12, onDark: true)
                            SkeletonBar(width: 180, height: 12, onDark: true)
                        }
                        .padding(18)
                    }
                ForEach(0..<4, id: \.self) { _ in
                    Card {
                        SkeletonBar(width: 100, height: 10)
                        SkeletonBar(height: 18)
                        SkeletonBar(width: 200, height: 12)
                    }
                }
            }
            .padding()
        }
        .scrollDisabled(true)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading")
    }
}

/// One shimmering placeholder bar. Holds still when Reduce Motion is on.
struct SkeletonBar: View {
    var width: CGFloat? = nil
    var height: CGFloat = 12
    var onDark = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var lit = false

    var body: some View {
        RoundedRectangle(cornerRadius: min(6, height / 2))
            .fill(onDark ? Color.white.opacity(0.14) : Color.primary.opacity(0.08))
            .frame(width: width, height: height)
            .frame(maxWidth: width == nil ? .infinity : nil, alignment: .leading)
            .opacity(lit ? 0.55 : 1)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { lit = true }
            }
    }
}

/// A button title with a small spinner while its work runs. The spinner sits beside the text, so nothing else moves.
struct BusyLabel: View {
    let title: String
    let busy: Bool

    var body: some View {
        HStack(spacing: 8) {
            if busy { ProgressView().controlSize(.small) }
            Text(title)
        }
    }
}

struct SectionLabel: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(.secondary).tracking(0.5)
    }
}

/// Loading, error and retry around a screen's fetch.
struct Loader<T, Content: View>: View {
    let load: () async throws -> T
    @ViewBuilder var content: (T, @escaping () async -> Void) -> Content
    @State private var value: T?
    @State private var error: String?
    @EnvironmentObject private var session: SessionModel

    var body: some View {
        Group {
            if let value {
                content(value, reload)
            } else if let error {
                ContentUnavailableView {
                    Label("Can't load this", systemImage: "exclamationmark.triangle")
                } description: {
                    Text(error)
                } actions: {
                    Button("Try again") { Task { await reload() } }
                }
            } else {
                SkeletonScreen()
            }
        }
        .task { if value == nil { await reload() } }
    }

    private func reload() async {
        do {
            value = try await load()
            error = nil
        } catch APIError.signedOut {
            session.signedOut()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
