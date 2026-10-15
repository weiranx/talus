import SwiftUI

/// Yes or no on a coach reply, kept in the coach log next to the prompt and the data. Tapping
/// the chosen answer again clears it.
struct CoachFeedback: View {
    let logId: Int
    var dark = false
    @EnvironmentObject private var api: API
    @State private var rating: Int?
    @State private var failed = false

    init(logId: Int, initial: Int?, dark: Bool = false) {
        self.logId = logId
        self.dark = dark
        _rating = State(initialValue: initial)
    }

    var body: some View {
        HStack(spacing: 8) {
            Text("Useful?")
            choice("Yes", 1)
            choice("No", -1)
            if failed { Text("Could not save.") }
        }
        .font(.caption)
        .foregroundStyle(dark ? .white.opacity(0.6) : .secondary)
    }

    private func choice(_ title: String, _ v: Int) -> some View {
        let on = rating == v
        return Button(title) { Task { await rate(v) } }
            .font(.caption.weight(.medium))
            .padding(.horizontal, 12).frame(minHeight: 30)
            .foregroundStyle(on ? (dark ? Color(hex: 0x15211D) : Color(uiColor: .systemBackground)) : (dark ? .white.opacity(0.85) : .primary))
            .background(on ? (dark ? Color.white : Color.primary) : .clear, in: Capsule())
            .overlay(Capsule().stroke(dark ? Color.white.opacity(0.3) : Color.secondary.opacity(0.35)))
            .buttonStyle(.plain)
            .accessibilityAddTraits(on ? .isSelected : [])
    }

    private func rate(_ v: Int) async {
        struct Body: Encodable { let rating: Int? }
        let next = rating == v ? nil : v
        do {
            try await api.post("/api/coach/feedback/\(logId)", Body(rating: next))
            rating = next
            failed = false
        } catch {
            failed = true
        }
    }
}

/// The coach label under a reply: who wrote it, its AI cost when the admin shows it, and feedback.
struct CoachFooter: View {
    let source: String
    let reason: String?
    let what: String
    let logId: Int?
    let feedback: Int?
    let cost: AICost?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(PlanText.coachNote(source, reason, what)).font(.caption).foregroundStyle(.secondary)
            if let cost { Text(cost.text).font(.caption2.monospacedDigit()).foregroundStyle(.secondary) }
            if let logId { CoachFeedback(logId: logId, initial: feedback) }
        }
    }
}

/// Question and answer rows, the way the web lays out a coach read.
struct CoachRead: View {
    let rows: [(String, String)]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(rows, id: \.0) { q, a in
                VStack(alignment: .leading, spacing: 4) {
                    Text(q).font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
                    CoachText(text: a)
                }
            }
        }
    }
}

/// Coach text, rendered for reading: paragraphs, bulleted and numbered lists, and **bold** for the
/// numbers that matter. The same small subset of markdown the web app renders
/// (web/components/CoachText.tsx), so a stray "*" or "_" in a reply shows as itself.
struct CoachText: View {
    let text: String

    enum Block: Equatable { case paragraph(String), bullet(String), numbered(String, String) }

    static func blocks(_ text: String) -> [Block] {
        var out: [Block] = []
        var para: [String] = []
        // The number the next item of a running numbered list gets; nil outside one.
        var next: Int?
        func flush() { if !para.isEmpty { out.append(.paragraph(para.joined(separator: "\n"))); para = [] } }
        for raw in text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
            .components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { flush(); next = nil; continue }
            if let r = line.range(of: #"^[-*•]\s+"#, options: .regularExpression) {
                flush(); next = nil
                out.append(.bullet(String(line[r.upperBound...])))
            } else if let r = line.range(of: #"^\d{1,2}[.)]\s+"#, options: .regularExpression) {
                flush()
                // Items count on from the first one's number, as an HTML list does; a list split
                // by a blank line starts again from the number written.
                let written = Int(line.prefix { $0.isNumber }) ?? 1
                let n = next ?? written
                next = n + 1
                out.append(.numbered("\(n).", String(line[r.upperBound...])))
            } else {
                let heading = line.range(of: #"^#{1,4}\s+"#, options: .regularExpression)
                let t = heading.map { "**\(line[$0.upperBound...].replacingOccurrences(of: "**", with: ""))**" } ?? line
                // An indented line right after a list item continues that item.
                if para.isEmpty, raw.first?.isWhitespace == true, let last = out.last {
                    switch last {
                    case .bullet(let item): out[out.count - 1] = .bullet(item + " " + t); continue
                    case .numbered(let n, let item): out[out.count - 1] = .numbered(n, item + " " + t); continue
                    case .paragraph: break
                    }
                }
                next = nil
                para.append(t)
            }
        }
        flush()
        return out
    }

    private static let bold = try! Regex(#"\*\*([^*]+?)\*\*"#)

    /// Only **bold**; everything else is shown as written.
    static func inline(_ s: String) -> AttributedString {
        var out = AttributedString()
        var at = s.startIndex
        for m in s.matches(of: bold) {
            out += AttributedString(String(s[at..<m.range.lowerBound]))
            var b = AttributedString(String(s[m.range].dropFirst(2).dropLast(2)))
            b.inlinePresentationIntent = .stronglyEmphasized
            out += b
            at = m.range.upperBound
        }
        out += AttributedString(String(s[at...]))
        return out
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(Self.blocks(text).enumerated()), id: \.offset) { _, b in
                switch b {
                case .paragraph(let t):
                    Text(Self.inline(t))
                case .bullet(let t):
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("•").foregroundStyle(.secondary)
                        Text(Self.inline(t))
                    }
                case .numbered(let n, let t):
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(n).monospacedDigit().foregroundStyle(.secondary)
                        Text(Self.inline(t))
                    }
                }
            }
        }
        .lineSpacing(3)
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Placeholder rows while the coach writes.
struct CoachPending: View {
    let rows: Int
    let line: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(0..<rows, id: \.self) { i in
                SkeletonBar(width: 90, height: 11)
                SkeletonBar(width: CGFloat(260 - i * 24), height: 12)
            }
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(line).font(.caption).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(line)
    }
}

/// The coach's read of one workout, on the activity page.
struct ActivityCoachCard: View {
    let id: Int
    @EnvironmentObject private var api: API
    @State private var reply: Coached<AnalysisReply>?
    @State private var done = false

    var body: some View {
        Card {
            SectionLabel("Coach's read")
            if let r = reply {
                CoachRead(rows: [("Did it do its job?", r.reply.job), ("Where did it go off?", r.reply.off),
                                 ("What did it cost?", r.reply.cost), ("What does it change?", r.reply.changes),
                                 ("Takeaway", r.reply.takeaway)])
                CoachFooter(source: r.source, reason: r.fallbackReason, what: "summary", logId: r.logId,
                            feedback: r.feedback, cost: r.cost)
            } else if done {
                Text("The coach could not read this workout.").foregroundStyle(.secondary)
            } else {
                CoachPending(rows: 5, line: "The coach is reading this workout.")
            }
        }
        .task {
            reply = try? await api.get("/api/coach/activity/\(id)", as: Coached<AnalysisReply>.self)
            done = true
        }
    }
}
