import SwiftUI

// Tap a number to see what went into it: the inputs, the formula and the arithmetic, all from the
// engine (web/components/Explain.tsx). A number is a link inside its Text, so it keeps flowing
// with the sentence around it; `.explainLinks()` on that Text opens the explanation over it.

enum Explain {
    static let scheme = "talus-explain"

    /// `text` as a tappable number, underlined with dots. `path` is the engine's explain route,
    /// such as "ledger/descent/fatigue".
    static func link(_ text: String, _ path: String) -> AttributedString {
        var a = AttributedString(text)
        a.link = URL(string: "\(scheme):\(path)")
        a.swiftUI.underlineStyle = Text.LineStyle(pattern: .dot, color: .secondary)
        return a
    }
}

private struct ExplainTarget: Identifiable {
    let path: String
    var id: String { path }
}

private struct ExplainLinks: ViewModifier {
    @State private var target: ExplainTarget?

    func body(content: Content) -> some View {
        content
            .tint(.primary)
            .environment(\.openURL, OpenURLAction { url in
                let prefix = "\(Explain.scheme):"
                guard url.absoluteString.hasPrefix(prefix) else { return .systemAction }
                target = ExplainTarget(path: String(url.absoluteString.dropFirst(prefix.count)))
                return .handled
            })
            .popover(item: $target) { t in
                ExplainPanel(path: t.path).presentationCompactAdaptation(.popover)
            }
    }
}

extension View {
    /// Opens the explanation for an `Explain.link` tapped inside this view.
    func explainLinks() -> some View { modifier(ExplainLinks()) }
}

/// The explanation itself, fetched when it opens.
struct ExplainPanel: View {
    let path: String
    @EnvironmentObject private var api: API
    @State private var data: Explanation?
    @State private var failed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if failed {
                Text("Could not load the explanation.")
            } else if let e = data {
                Text(e.title).fontWeight(.semibold)
                Text(e.formula)
                if !e.inputs.isEmpty {
                    Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 4) {
                        ForEach(e.inputs, id: \.label) { i in
                            GridRow {
                                Text(i.label).foregroundStyle(.secondary)
                                Text(i.value).monospacedDigit().gridColumnAlignment(.trailing)
                            }
                        }
                    }
                }
                ForEach(e.steps, id: \.self) { s in
                    Text(s).font(.footnote.monospaced())
                        .padding(.horizontal, 8).padding(.vertical, 4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(uiColor: .systemGroupedBackground), in: RoundedRectangle(cornerRadius: 6))
                }
                if let note = e.note { Text(note).foregroundStyle(.secondary) }
                if let unit = e.unit { Text(unit).foregroundStyle(.secondary) }
            } else {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Loading…").foregroundStyle(.secondary)
                }
            }
        }
        .font(.footnote)
        .fixedSize(horizontal: false, vertical: true)
        .frame(width: 300, alignment: .leading)
        .padding(16)
        .task {
            do {
                data = try await api.get("/api/explain/\(path)", as: Explanation.self)
            } catch {
                failed = true
            }
        }
    }
}
