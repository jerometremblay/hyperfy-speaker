# Hyperfy Speaker

Keep this project separate from the Hyperfy engine checkout. The integration
patch in `hyperfy/` is the only engine change required.

Prefer BlackHole's existing Core Audio driver and the official LiveKit SDK.
Do not replace either with a custom audio driver or streaming protocol.

Never log tokens, environment files, audio samples, or Keychain contents.
World tokens are stored in macOS Keychain, not in configuration JSON.
Use an isolated world and test tone for streaming verification.

Do not create commits or publish this repository unless explicitly requested.
For authorized GitHub operations, verify `gh api user --jq .login` is
`jerometremblay-bot`. Use that bot's author and committer identity, and sign
every authorized commit with `git -c user.signingkey=C81228C654940396 commit -S`.
