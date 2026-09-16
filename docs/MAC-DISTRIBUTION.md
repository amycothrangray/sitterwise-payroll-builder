# Standalone Mac distribution

The standalone app bundles Python, openpyxl and pypdf, and runs on Intel and Apple Silicon Macs with macOS 11 or later. Operators do not install Python or use GitHub Desktop.

Build with a universal2 Python and `packaging/requirements-macos.txt` installed:

```
python3 package_macos.py
```

The build uses a temporary directory by default to avoid cloud-folder metadata invalidating code signatures. Set `SITTERWISE_MAC_BUILD_DIR` to choose another local build folder. Set `SITTERWISE_CODESIGN_IDENTITY` to the exact Developer ID Application identity to produce a signed app. Without it the build is ad-hoc signed for development only. Apple notarization and stapling remain separate release steps.

Only application code and explicit assets enter the bundle. Payroll databases, uploaded exports, private transfer files, and Git metadata are excluded. Package the app in a DMG with an Applications shortcut, notarize it using `xcrun notarytool submit`, staple the accepted ticket, and verify it before publishing a download.

## Moving payroll history

In the standalone app, records live under `~/Library/Application Support/Sitterwise Payroll`, outside the app bundle. Replacing the app does not replace payroll history. The source-code launcher continues to use its existing `data/` folder. `SITTERWISE_PAYROLL_DATA_DIR` overrides the complete data directory for isolated tests.

Settings has **Download private history file** and **Restore history on this Mac**. The private `.sitterwise` archive includes a consistent SQLite backup, every saved run's source export, rules, and OnPay mapping. It contains confidential payroll information and is not encrypted; transfer it privately. Never publish it beside a public app download.

The copied database uses relative source paths. Restore checks file hashes, database integrity, and record counts before changing an empty destination. Restore refuses an app that already contains records. Existing history is not merged or overwritten. The original data is unchanged by making a backup.

The standalone launcher saves an automatic history snapshot on the first launch each day when payroll history exists. A backup on the same Mac is not protection against losing that Mac; keep a private off-device copy too.

## Release checks

- Run the tests with the packaged Python version.
- Verify both executable architectures and the complete Developer ID signature.
- Launch the app with no external Python environment and an isolated data folder.
- Restore history through the Settings screen and reopen a completed payroll.
- Verify totals, recurring entries, paid-booking records, and a backup after restart.
- Verify no databases or real source exports are in the software bundle.
- Confirm notarization is accepted, staple the ticket, and assess the final DMG.

The handoff includes the existing payroll workflow: paycheck memos are entered separately in OnPay, and unmapped recurring pay still requires manual entry. Packaging does not turn those steps into an automatic integration.
