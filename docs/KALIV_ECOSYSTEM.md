# Kaliv ecosystem integration map

Snapshot: 2026-10-23. This navigation document is not evidence of production readiness.

## Related projects and boundaries

- [ModelRig](https://github.com/Ternedal/ModelRig) — backend orchestration, authenticated APIs, cognition, memory and action approval.
- [KalivNexus](https://github.com/Ternedal/KalivNexus) — **proposed** external API discovery and capability broker; repository bootstrap is not proof of an implemented integration.
- [BodyRig](https://github.com/Ternedal/BodyRig) — source-derived body identity and motor state.
- [VoiceRig](https://github.com/Ternedal/VoiceRig) — voice-profile and TTS authority.
- [VisionRig](https://github.com/Ternedal/VisionRig) — structured perception; observations alone do not authorize memory writes or actions.
- [LanguageRig](https://github.com/Ternedal/LanguageRig) — Danish model adaptation and evaluation.
- [Kaliv](https://github.com/Ternedal/Kaliv) — public product website.
- [KalivHub](https://github.com/Ternedal/KalivHub) and [KalivHub-Receiver](https://github.com/Ternedal/KalivHub-Receiver) — Home Assistant/Cast sender and display-only receiver.
- [SkyPlayer-Engine](https://github.com/Ternedal/SkyPlayer-Engine) — shared XR/media playback primitives.

## Integration requirements

1. Preserve each subsystem's data authority and versioned contracts; ModelRig retains policy and action approval.
2. Treat Nexus provider catalogs as untrusted discovery inputs; require explicit provider activation, credentials isolation, quota controls, network egress protection and redacted audits.
3. Distinguish proposed, implemented, software-tested, physically tested and released functionality.
4. Record exact commit SHAs and physical evidence for cross-repository release qualification.
5. Do not commit tokens, personal media or licensed training assets. Generated media should reside on configured external storage and be retrieved through authenticated client APIs.
6. Documentation changes alone must not activate features or alter release gates.

## Verification backlog

- [ ] Validate this repository's live API, installation, configuration and rollback against current source.
- [ ] Update operational examples and compatibility matrix on tested exact revisions.
- [ ] Confirm end-to-end hardware acceptance and link evidence before marking features released.
