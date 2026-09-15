// cswap menu bar panel.
//
// A status item ("CS") in the macOS menu bar. Clicking it drops down a
// popover — the same kind the system Displays / Wi-Fi items use — holding the
// live `cswap` TUI in an embedded terminal, so the dashboard is one click away
// without a Dock icon or a Terminal window. Right-click: restart / quit.
//
// Configuration arrives in the environment (set by panel.py, whether launched
// through launchd or `cswap panel` in the foreground):
//
//   CSWAP_PANEL_PROGRAM   absolute path of the `cswap` console script
//   CSWAP_PANEL_DATA_DIR  cswap's backup root (sequence.json lives there)
//
// The popover is sized to the account count: up to MAX_CARDS full cards plus
// the menu. The TUI is told the same cap through CSWAP_MAX_ACCOUNT_CARDS so
// that past MAX_CARDS the accounts panel scrolls instead of pushing the menu
// off the bottom. CARD_ROWS / CARD_GAP / PANEL_CHROME mirror the constants in
// claude_swap/tui/dashboard.py — keep them in sync.

import AppKit
import SwiftTerm

let env = ProcessInfo.processInfo.environment
let HOME = NSHomeDirectory()
let DATA_DIR = env["CSWAP_PANEL_DATA_DIR"] ?? "\(HOME)/.claude-swap-backup"

let COLS = 104
let MAX_CARDS = 3        // accounts shown before the accounts panel scrolls
let CARD_ROWS = 4        // header + 5h + 7d + one per-model row
let CARD_GAP = 1         // blank row between full cards
let PANEL_CHROME = 3     // accounts panel padding (2) + bottom border (1)
let MENU_ROWS = 14       // menu title (2) + menu padding (1) + 9 entries + footer (1) + 1 spare

func resolveCswap() -> String? {
    if let p = env["CSWAP_PANEL_PROGRAM"], FileManager.default.isExecutableFile(atPath: p) {
        return p
    }
    let path = env["PATH"] ?? "\(HOME)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    for dir in path.split(separator: ":") {
        let candidate = "\(dir)/cswap"
        if FileManager.default.isExecutableFile(atPath: candidate) { return candidate }
    }
    return nil
}

func accountCount() -> Int {
    let url = URL(fileURLWithPath: "\(DATA_DIR)/sequence.json")
    guard let data = try? Data(contentsOf: url),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let seq = obj["sequence"] as? [Any] else { return 1 }
    return max(seq.count, 1)
}

final class TermVC: NSViewController, LocalProcessTerminalViewDelegate {
    var term: LocalProcessTerminalView!
    var running = false

    override func loadView() {
        term = LocalProcessTerminalView(frame: NSRect(x: 0, y: 0, width: 800, height: 400))
        term.processDelegate = self
        term.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)  // SF Mono, like Terminal.app
        term.nativeBackgroundColor = NSColor(calibratedWhite: 0.09, alpha: 1)
        term.nativeForegroundColor = NSColor(calibratedWhite: 0.9, alpha: 1)
        // SwiftTerm's custom vector renderer for box-drawing characters
        // (U+2500–257F — the usage bars' ━ ─ ┃ ╸) misplaces the text that
        // follows a bar: the "88%" landed on top of the threshold tick. SF Mono
        // has these glyphs at exactly one cell each, so let the font draw them.
        term.customBlockGlyphs = false
        term.caretColor = .clear   // the TUI has no text cursor; hide the blinking block
        term.caretTextColor = NSColor(calibratedWhite: 0.9, alpha: 1)
        view = term
    }

    /// Size the terminal for up to MAX_CARDS accounts plus the menu; returns the pixel size.
    func fitToAccounts() -> NSSize {
        let shown = min(accountCount(), MAX_CARDS)
        let cards = shown * CARD_ROWS + max(0, shown - 1) * CARD_GAP
        term.resize(cols: COLS, rows: PANEL_CHROME + cards + MENU_ROWS)
        let f = term.getOptimalFrameSize()
        return NSSize(width: ceil(f.width), height: ceil(f.height))
    }

    func start() {
        guard !running else { return }
        guard let cswap = resolveCswap() else {
            term.feed(text: "cswap not found.\r\nSet CSWAP_PANEL_PROGRAM or put cswap on PATH.\r\n")
            return
        }
        running = true
        var child = env
        child["TERM"] = "xterm-256color"
        child["COLORTERM"] = "truecolor"
        if child["LANG"] == nil { child["LANG"] = "en_US.UTF-8" }
        child["HOME"] = HOME
        child["CSWAP_MAX_ACCOUNT_CARDS"] = String(MAX_CARDS)
        let dirs = [URL(fileURLWithPath: cswap).deletingLastPathComponent().path,
                    "\(HOME)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
        child["PATH"] = dirs.joined(separator: ":")
        let envArr = child.map { "\($0.key)=\($0.value)" }
        term.startProcess(executable: cswap, args: ["tui"], environment: envArr, execName: "cswap")
    }

    func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}
    func setTerminalTitle(source: LocalProcessTerminalView, title: String) {}
    func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}
    func processTerminated(source: TerminalView, exitCode: Int32?) {
        running = false
        DispatchQueue.main.async { (NSApp.delegate as? App)?.popover.performClose(nil) }
    }
}

final class App: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!
    let popover = NSPopover()
    let vc = TermVC()

    func applicationDidFinishLaunching(_ n: Notification) {
        popover.contentViewController = vc
        popover.behavior = .transient
        popover.animates = true
        popover.appearance = NSAppearance(named: .darkAqua)
        _ = vc.view
        popover.contentSize = vc.fitToAccounts()
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let b = item.button {
            b.title = "CS"
            b.font = NSFont.monospacedSystemFont(ofSize: 13, weight: .bold)
            b.toolTip = "cswap dashboard"
            b.target = self
            b.action = #selector(clicked)
            b.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }
    }

    @objc func clicked() {
        if let e = NSApp.currentEvent, e.type == .rightMouseUp {
            let m = NSMenu()
            m.addItem(withTitle: "Restart dashboard", action: #selector(restart), keyEquivalent: "")
            m.addItem(NSMenuItem.separator())
            m.addItem(withTitle: "Quit cswap panel", action: #selector(quit), keyEquivalent: "")
            for i in m.items { i.target = self }
            item.menu = m; item.button?.performClick(nil); item.menu = nil
            return
        }
        if popover.isShown { popover.performClose(nil) } else { show() }
    }

    func show() {
        guard let b = item.button else { return }
        popover.contentSize = vc.fitToAccounts()   // re-measure: accounts may have been added
        popover.show(relativeTo: b.bounds, of: b, preferredEdge: .minY)
        NSApp.activate(ignoringOtherApps: true)
        vc.start()
        vc.view.window?.makeFirstResponder(vc.term)
    }

    @objc func restart() {
        popover.performClose(nil)
        vc.term.terminate()
        vc.running = false
    }

    @objc func quit() { vc.term.terminate(); NSApp.terminate(nil) }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
