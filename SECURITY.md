# Security policy

## Supported versions

Security fixes are applied to the latest tagged release and the `main` branch.

## Report a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do not
open a public issue containing exploit details, credentials, private prompts, or
repository data.

Include the affected version, operating system, NTM/Codex/Claude versions, a
minimal reproduction, and the expected security boundary.

## Boundary reminder

This project validates orchestration state and detects repository-visible scope
violations. It does not sandbox unattended Fable workers, control model-provider
retention, or form a security boundary against malicious code running under the
same operating-system account.
