#!/bin/sh
# Start a fresh Zotero profile with the local API on and one pre-authorized write key.
set -eu

: "${ZOTERO_TEST_API_KEY:?ZOTERO_TEST_API_KEY must be set}"

mkdir -p "$HOME/profile" "$HOME/data"
cat > "$HOME/profile/user.js" <<'EOF'
user_pref("extensions.zotero.httpServer.enabled", true);
user_pref("extensions.zotero.httpServer.port", 23119);
user_pref("extensions.zotero.httpServer.localAPI.enabled", true);
user_pref("extensions.zotero.firstRun2", false);
user_pref("extensions.zotero.firstRunGuidance", false);
user_pref("extensions.zotero.useDataDir", true);
user_pref("extensions.zotero.dataDir", "/home/zotero/data");
user_pref("extensions.zotero.sync.autoSync", false);
user_pref("extensions.zotero.automaticScraperUpdates", false);
user_pref("app.update.auto", false);
user_pref("app.update.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
EOF
# Zotero reads and caches this file at startup, so it must exist before launch.
printf '{"keys":[{"key":"%s","appName":"Disposable tests","remember":true,"createdAt":"2026-01-01T00:00:00.000Z"}]}\n' \
    "$ZOTERO_TEST_API_KEY" > "$HOME/profile/localAPIKeys.json"

python3 /opt/relay.py &
Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp -ac &
while [ ! -S /tmp/.X11-unix/X99 ]; do sleep 0.05; done
export DISPLAY=:99 MOZ_ALLOW_DOWNGRADE=1 MOZ_LEGACY_PROFILES=1 MOZ_ENABLE_WAYLAND=1
ulimit -n 4096
# Exec the binary directly so it receives stop signals as PID 1.
exec /opt/zotero/zotero-bin -app /opt/zotero/app/application.ini -no-remote -profile "$HOME/profile"
