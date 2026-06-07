# Releasing Connect

End-to-end release flow for the ToneForge Connect macOS app. The
runbook for the next person to cut a release — including someone
who is not the original author.

## Decisions baked in

- **Signing identity**: Apple Developer ID Application certificate
  installed in the keychain of the release machine (or the CI runner).
- **Sparkle EdDSA key**: private key held *only* as the GitHub Actions
  secret `SPARKLE_ED_PRIVATE_KEY`. Local machines cannot publish
  appcast entries — they can sign + notarize binaries for testing,
  but the release-train metadata is CI-only. (See `ONBOARDING_AUDIT.md`
  §F6 for the rationale.)
- **Appcast host**: GitHub Pages, served from `connect/docs/` on the
  default branch. URL is pinned in `Resources/Info.plist` as
  `SUFeedURL`. Changing it requires a rebuild + reship.

## One-time setup

1. Generate the EdDSA keypair using Sparkle's `generate_keys`:
   ```
   # Sparkle ships generate_keys inside the SwiftPM artifact bundle.
   ls .build/artifacts/sparkle/Sparkle/bin/generate_keys
   .build/artifacts/sparkle/Sparkle/bin/generate_keys
   ```
   The tool emits a base64 public key to stdout and stores the
   private key in the macOS keychain. Export the private key:
   ```
   security find-generic-password -a ed25519 -s "https://sparkle-project.org" -w
   ```
2. Store the **private** key as a GitHub Actions secret:
   - Repo Settings → Secrets and variables → Actions → New secret
   - Name: `SPARKLE_ED_PRIVATE_KEY`
   - Value: the base64 string from step 1
3. Store the **public** key as a repo variable so build_release.sh
   can stamp it into Info.plist:
   - Same page → Variables → New variable
   - Name: `CONNECT_SPARKLE_PUBLIC_KEY`
   - Value: the base64 public key (this one is safe to publish)
4. Turn on GitHub Pages:
   - Settings → Pages → Source: deploy from branch
   - Branch: `main` / folder: `/docs` (root-level docs/ symlink not
     required — Pages serves from any committed directory). The
     appcast path becomes
     `https://<user>.github.io/<repo>/connect/appcast.xml`.

## Per-release flow

1. Bump `CFBundleShortVersionString` and `CFBundleVersion` in
   `connect/Resources/Info.plist`. These are the single source of
   truth — `build_release.sh` reads them.
2. Commit the bump on `main`. Tag the release:
   `git tag connect-vX.Y.Z && git push --tags`.
3. The `connect-release` GitHub Actions workflow takes over:
   - Builds the universal binary
   - Code-signs and notarizes the DMG
   - Signs an appcast entry with `SPARKLE_ED_PRIVATE_KEY`
   - Prepends the new `<item>` to `connect/docs/appcast.xml`
   - Commits and pushes the appcast update to `main`
   - Creates the GitHub Release with the DMG attached

GitHub Pages picks up the new appcast within ~30 seconds. The
running Connect builds with `SUEnableAutomaticChecks` set will
detect the new version on their next scheduled check (default
86400s) or sooner via the menu's *Check for Updates…*.

## Verifying a release

Before announcing a release publicly:

1. Download the DMG from the GitHub Release page.
2. `spctl --assess --verbose=4 /Volumes/Connect/Connect.app` →
   must report `source=Notarized Developer ID`.
3. Open `https://<user>.github.io/<repo>/connect/appcast.xml` in
   a browser; confirm the new item is at the top.
4. On a test Mac running the previous release, click
   *Check for Updates…*. Sparkle should download the new DMG,
   verify the EdDSA signature, and offer to install on quit.
5. Confirm that on relaunch, the new build runs and
   `CFBundleShortVersionString` matches what was tagged.

## Rolling back a release

Sparkle has no native rollback — it only walks versions forward.
If a release has to be pulled:

1. Delete the offending `<item>` from `connect/docs/appcast.xml`
   on `main`. Pages will re-serve the file within ~30s.
2. Cut a new release with a higher version that is a copy of the
   prior known-good code.
3. Already-updated users have to wait for the new release; there
   is no way to push a downgrade.

This is the cost of EdDSA-signed Sparkle: an attacker with a
compromised CI cannot ship a backdoor (no private key locally),
but neither can a release manager push an emergency unwind. Plan
fixes forward.
