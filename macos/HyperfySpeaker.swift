import Cocoa
import Security

private let keychainService = "org.hyperfy.speaker"

func storeToken(_ token: String, world: String) throws {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: keychainService,
        kSecAttrAccount as String: world,
    ]
    let data = [kSecValueData as String: Data(token.utf8)]
    var result = SecItemUpdate(query as CFDictionary, data as CFDictionary)
    if result == errSecItemNotFound {
        var item = query
        item.merge(data) { _, new in new }
        result = SecItemAdd(item as CFDictionary, nil)
    }
    guard result == errSecSuccess else {
        throw NSError(domain: NSOSStatusErrorDomain, code: Int(result))
    }
}

func loadToken(world: String) -> String? {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: keychainService,
        kSecAttrAccount as String: world,
        kSecReturnData as String: true,
        kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var result: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
          let data = result as? Data else { return nil }
    return String(data: data, encoding: .utf8)
}

final class SpeakerApp: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var statusMenu: NSMenuItem!
    private var startMenu: NSMenuItem!
    private var stopMenu: NSMenuItem!
    private var workerProcess: Process?
    private var settingsWindow: NSWindow?
    private var statusWindow: NSWindow?
    private let statusLabel = NSTextField(labelWithString: "Stopped")
    private var startButton: NSButton!
    private var stopButton: NSButton!
    private let worldField = NSTextField()
    private let speakerField = NSTextField()
    private let tokenField = NSSecureTextField()
    private let devicePicker = NSPopUpButton()
    private let inputModePicker = NSPopUpButton()
    private var errorField = NSTextField(labelWithString: "")
    private var outputBuffer = Data()
    private var settingsBusy = false
    private var operationBusy = false
    private var terminating = false

    private var workerURL: URL {
        Bundle.main.resourceURL!.appendingPathComponent("speaker-worker/speaker-worker")
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let mainMenu = NSMenu()
        let applicationMenu = NSMenu()
        let applicationItem = NSMenuItem(title: "Hyperfy Speaker", action: nil, keyEquivalent: "")
        let showItem = applicationMenu.addItem(
            withTitle: "Show Hyperfy Speaker", action: #selector(showStatus), keyEquivalent: "0"
        )
        showItem.target = self
        let settingsItem = applicationMenu.addItem(
            withTitle: "Settings…", action: #selector(showSettings), keyEquivalent: ","
        )
        settingsItem.target = self
        applicationMenu.addItem(NSMenuItem.separator())
        let quitItem = applicationMenu.addItem(
            withTitle: "Quit Hyperfy Speaker", action: #selector(quit), keyEquivalent: "q"
        )
        quitItem.target = self
        applicationItem.submenu = applicationMenu
        mainMenu.addItem(applicationItem)
        NSApp.mainMenu = mainMenu
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = NSImage(systemSymbolName: "speaker.wave.2", accessibilityDescription: "Hyperfy Speaker")
        let menu = NSMenu()
        menu.autoenablesItems = false
        statusMenu = menu.addItem(withTitle: "Stopped", action: nil, keyEquivalent: "")
        menu.addItem(NSMenuItem.separator())
        startMenu = menu.addItem(withTitle: "Start Streaming", action: #selector(startStreaming), keyEquivalent: "")
        stopMenu = menu.addItem(withTitle: "Stop Streaming", action: #selector(stopStreaming), keyEquivalent: "")
        stopMenu.isEnabled = false
        menu.addItem(withTitle: "Settings…", action: #selector(showSettings), keyEquivalent: ",")
        menu.addItem(withTitle: "Create Speaker in World", action: #selector(createSpeaker), keyEquivalent: "")
        menu.addItem(withTitle: "Open Sound Settings", action: #selector(openSoundSettings), keyEquivalent: "")
        menu.addItem(NSMenuItem.separator())
        menu.addItem(withTitle: "Quit Hyperfy Speaker", action: #selector(quit), keyEquivalent: "q")
        for item in menu.items { item.target = self }
        statusItem.menu = menu
        let config = configuration()
        if !(config["world_url"] ?? "").isEmpty, !(config["speaker_id"] ?? "").isEmpty {
            showStatus()
            startStreaming()
        } else {
            showSettings()
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showStatus()
        return true
    }

    @objc private func showStatus() {
        if statusWindow == nil {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 480, height: 225),
                styleMask: [.titled, .closable], backing: .buffered, defer: false
            )
            window.title = "Hyperfy Speaker — Streaming"
            window.isReleasedWhenClosed = false
            let content = window.contentView!
            statusLabel.font = .boldSystemFont(ofSize: 18)
            statusLabel.frame = NSRect(x: 20, y: 175, width: 440, height: 26)
            content.addSubview(statusLabel)
            let help = NSTextField(wrappingLabelWithString:
                "Choose Hyperfy Speaker in Music’s or your iPhone’s AirPlay menu when using AirPlay input.\nHyperfy audio output: your headphones or Mac speakers.\nKeep this app running while playing music.")
            help.frame = NSRect(x: 20, y: 65, width: 440, height: 95)
            content.addSubview(help)
            startButton = NSButton(title: "Start Streaming", target: self, action: #selector(startStreaming))
            stopButton = NSButton(title: "Stop Streaming", target: self, action: #selector(stopStreaming))
            let settingsButton = NSButton(title: "Settings…", target: self, action: #selector(showSettings))
            for (index, button) in [startButton!, stopButton!, settingsButton].enumerated() {
                button.bezelStyle = .rounded
                button.frame = NSRect(x: 20 + CGFloat(index * 148), y: 20, width: 140, height: 30)
                content.addSubview(button)
            }
            statusWindow = window
            window.center()
        }
        setStatus(statusMenu.title)
        NSApp.activate(ignoringOtherApps: true)
        statusWindow?.makeKeyAndOrderFront(nil)
    }

    private func setStatus(_ title: String) {
        statusMenu.title = title
        statusLabel.stringValue = title
        let canStart = workerProcess == nil && !operationBusy
        startMenu.isEnabled = canStart
        stopMenu.isEnabled = workerProcess != nil
        startButton?.isEnabled = canStart
        stopButton?.isEnabled = workerProcess != nil
    }

    private func configPath() -> String {
        ProcessInfo.processInfo.environment["HYPERFY_SPEAKER_CONFIG"]
            ?? NSHomeDirectory() + "/Library/Application Support/Hyperfy Speaker/config.json"
    }

    private func configuration() -> [String: String] {
        guard let data = FileManager.default.contents(atPath: configPath()),
              let values = try? JSONSerialization.jsonObject(with: data) as? [String: String] else { return [:] }
        return values
    }

    private func command(_ arguments: [String], completion: @escaping (Int32, Data) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            let process = Process()
            let pipe = Pipe()
            process.executableURL = workerURL
            process.arguments = arguments
            process.environment = workerEnvironment()
            process.standardOutput = pipe
            process.standardError = FileHandle.nullDevice
            do {
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                DispatchQueue.main.async { completion(process.terminationStatus, data) }
            } catch {
                DispatchQueue.main.async { completion(1, Data("{\"error\":\"Could not launch audio helper.\"}".utf8)) }
            }
        }
    }

    private func workerEnvironment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["HYPERFY_SPEAKER_AIRPLAY_BINARY"] = Bundle.main.resourceURL!
            .appendingPathComponent("shairport-sync").path
        if environment["HYPERFY_SPEAKER_TOKEN"] == nil,
           let world = configuration()["world_url"], let token = loadToken(world: world) {
            environment["HYPERFY_SPEAKER_TOKEN"] = token
        }
        return environment
    }

    private func json(_ data: Data) -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
    }

    private func message(_ data: Data) -> String {
        json(data)["error"] as? String ?? "Operation failed. Check the world URL and its write token."
    }

    @objc private func startStreaming() {
        guard workerProcess == nil, !operationBusy else { return }
        let config = configuration()
        guard !(config["world_url"] ?? "").isEmpty, !(config["speaker_id"] ?? "").isEmpty else {
            showSettings()
            errorField.stringValue = "Save your settings, then create a speaker in the world."
            return
        }
        let process = Process()
        let pipe = Pipe()
        process.executableURL = workerURL
        process.arguments = ["run"]
        process.environment = workerEnvironment()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        outputBuffer = Data()
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async { self?.consume(data) }
        }
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, self.workerProcess === process else { return }
                self.workerProcess = nil
                self.startMenu.isEnabled = true
                self.stopMenu.isEnabled = false
                self.statusItem.button?.image = NSImage(systemSymbolName: "speaker.wave.2", accessibilityDescription: "Stopped")
                self.setStatus(self.statusMenu.title == "Streaming failed" ? "Streaming failed" : "Stopped")
                if self.terminating { NSApp.terminate(nil) }
            }
        }
        do {
            try process.run()
            workerProcess = process
            startMenu.isEnabled = false
            stopMenu.isEnabled = true
            setStatus("Connecting…")
        } catch {
            setStatus("Audio helper could not start")
        }
    }

    private func consume(_ data: Data) {
        outputBuffer.append(data)
        while let newline = outputBuffer.firstIndex(of: 10) {
            let line = Data(outputBuffer[..<newline])
            outputBuffer.removeSubrange(...newline)
            let event = json(line)
            guard let state = event["state"] as? String else { continue }
            switch state {
            case "streaming":
                setStatus("Streaming to Hyperfy")
                statusItem.button?.image = NSImage(systemSymbolName: "speaker.wave.2.fill", accessibilityDescription: "Streaming")
            case "airplay_ready":
                setStatus("AirPlay ready — Hyperfy Speaker")
                statusItem.button?.image = NSImage(systemSymbolName: "airplay.audio", accessibilityDescription: "AirPlay ready")
            case "connecting": setStatus("Connecting…")
            case "reconnecting": setStatus("Reconnecting…")
            case "error":
                setStatus("Streaming failed")
                showSettings()
                errorField.stringValue = event["error"] as? String ?? "Streaming failed."
            default: setStatus("Stopped")
            }
        }
        if outputBuffer.count > 65536 { outputBuffer.removeAll() }
    }

    @objc private func stopStreaming() {
        workerProcess?.terminate()
        setStatus("Stopping…")
    }

    @objc private func showSettings() {
        if settingsWindow == nil {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 520, height: 425),
                styleMask: [.titled, .closable], backing: .buffered, defer: false
            )
            window.title = "Hyperfy Speaker"
            window.isReleasedWhenClosed = false
            let content = window.contentView!
            let fields: [(String, NSView)] = [
                ("World URL", worldField), ("World write token", tokenField),
                ("Speaker instance ID", speakerField), ("Audio source", inputModePicker),
                ("Mac audio input", devicePicker),
            ]
            for (index, entry) in fields.enumerated() {
                let y = 370 - CGFloat(index * 48)
                let label = NSTextField(labelWithString: entry.0)
                label.frame = NSRect(x: 20, y: y + 4, width: 145, height: 20)
                entry.1.frame = NSRect(x: 168, y: y, width: 330, height: 27)
                content.addSubview(label)
                content.addSubview(entry.1)
            }
            inputModePicker.addItems(withTitles: ["Mac virtual output", "AirPlay — Hyperfy Speaker"])
            inputModePicker.target = self
            inputModePicker.action = #selector(inputModeChanged)
            worldField.placeholderString = "https://your-world.example"
            tokenField.placeholderString = "Leave blank to keep the saved Keychain token"
            speakerField.placeholderString = "Filled by Create Speaker in World"
            let help = NSTextField(wrappingLabelWithString:
                "AirPlay: choose Hyperfy Speaker in Music’s or your iPhone’s AirPlay menu. Mac virtual output: choose Hyperfy Speaker as your Mac’s sound output. In Hyperfy, select your physical speakers or headphones.")
            help.frame = NSRect(x: 20, y: 86, width: 478, height: 66)
            content.addSubview(help)
            errorField = NSTextField(wrappingLabelWithString: "")
            errorField.textColor = .systemRed
            errorField.font = .systemFont(ofSize: 11)
            errorField.frame = NSRect(x: 20, y: 42, width: 380, height: 44)
            content.addSubview(errorField)
            let button = NSButton(title: "Save", target: self, action: #selector(saveSettings))
            button.bezelStyle = .rounded
            button.frame = NSRect(x: 407, y: 16, width: 91, height: 30)
            content.addSubview(button)
            settingsWindow = window
            window.center()
        }
        let config = configuration()
        worldField.stringValue = config["world_url"] ?? ""
        speakerField.stringValue = config["speaker_id"] ?? ""
        tokenField.stringValue = ""
        inputModePicker.selectItem(at: config["input_mode"] == "airplay" ? 1 : 0)
        inputModeChanged()
        errorField.stringValue = ""
        devicePicker.removeAllItems()
        devicePicker.addItem(withTitle: config["device"] ?? "Hyperfy Speaker")
        command(["devices"]) { [weak self] _, data in
            guard let self, let devices = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return }
            let selected = self.devicePicker.titleOfSelectedItem
            for device in devices {
                if let name = device["name"] as? String,
                   self.devicePicker.item(withTitle: name) == nil { self.devicePicker.addItem(withTitle: name) }
            }
            if let selected { self.devicePicker.selectItem(withTitle: selected) }
        }
        NSApp.activate(ignoringOtherApps: true)
        settingsWindow?.makeKeyAndOrderFront(nil)
    }

    @objc private func inputModeChanged() {
        devicePicker.isEnabled = inputModePicker.indexOfSelectedItem == 0
    }

    @objc private func saveSettings() {
        guard !settingsBusy, !operationBusy, workerProcess == nil else {
            errorField.stringValue = "Stop streaming before changing settings."
            return
        }
        settingsBusy = true
        let token = tokenField.stringValue
        command([
            "configure", "--world", worldField.stringValue,
            "--speaker-id", speakerField.stringValue,
            "--device", devicePicker.titleOfSelectedItem ?? "Hyperfy Speaker",
            "--input-mode", inputModePicker.indexOfSelectedItem == 1 ? "airplay" : "device",
        ]) { [weak self] code, data in
            guard let self else { return }
            self.settingsBusy = false
            guard code == 0, let world = self.json(data)["world_url"] as? String else {
                self.errorField.stringValue = self.message(data)
                return
            }
            do {
                if !token.isEmpty { try storeToken(token, world: world) }
                self.tokenField.stringValue = ""
                self.errorField.stringValue = ""
                self.settingsWindow?.close()
                self.showStatus()
            } catch {
                self.errorField.stringValue = "Could not save the token in Keychain."
            }
        }
    }

    @objc private func createSpeaker() {
        guard workerProcess == nil, !operationBusy else { return }
        guard !(configuration()["world_url"] ?? "").isEmpty else {
            showSettings()
            errorField.stringValue = "Save the world URL and write token first."
            return
        }
        operationBusy = true
        startMenu.isEnabled = false
        setStatus("Creating speaker…")
        command(["setup"]) { [weak self] code, data in
            guard let self else { return }
            self.operationBusy = false
            self.startMenu.isEnabled = true
            if code == 0 {
                self.setStatus("Speaker ready")
                self.speakerField.stringValue = self.configuration()["speaker_id"] ?? ""
            } else {
                self.setStatus("Speaker setup failed")
                self.showSettings()
                self.errorField.stringValue = self.message(data)
            }
        }
    }

    @objc private func openSoundSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.Sound-Settings.extension") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func quit() {
        if workerProcess != nil {
            terminating = true
            stopStreaming()
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                if let process = self.workerProcess {
                    kill(process.processIdentifier, SIGKILL)
                }
                NSApp.terminate(nil)
            }
        } else { NSApp.terminate(nil) }
    }
}

let application = NSApplication.shared
let delegate = SpeakerApp()
application.delegate = delegate
application.run()
