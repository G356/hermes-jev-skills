# Image-preview PR readiness review

Reviewed original PR head `eec6b98f5901ff89134f202b0cd41bdfed7037c6` against main at `98ce26a`. Implementation, critical review and QA were performed in separate passes by the same reviewer; this is not independent review.

## Repairs with red/green regressions

- Asset references must occur in state, instruction or criterion string values. Object keys and the question type are not image references. Unicode is matched before JSON escaping; whitespace-only references are invalid.
- The labelled A/B runner now applies the shadow's sensitive-question skip before reading or sending its image.
- Cloud vision rechecks its opt-in immediately before sending, so a disable during image preparation prevents that upload. An already in-flight request cannot be recalled.

Six new regressions were observed failing before their fixes, then passing. The original feature had 25 targeted tests; the repaired feature has 31.

## Executed verification

- Full offline suite: 1,511 tests passed on macOS Python 3.9.6 and Python 3.11.15.
- `scripts/check_release.py`: clean. Its unchanged dashboard PNG remains an explicitly unscanned binary; it was not modified by this PR.
- `git diff --check`: clean.
- Disposable installation via `install.py`, with a separate HOME and Hermes home: installed image/cloud modules match the candidate, installed imports work, and cloud vision defaults to off. No live fleet installation, switch change or gateway restart occurred.
- Live synthetic check against TypeSafe: returned `jev-1.14.0`; square-present 0.98, nonexistent-circle 0.02, correct top-right quadrant at confidence 1.0, Box overlap 0.831 at confidence 0.97. All four checks passed; 1,164 input tokens and 182 ms reported on the final run. Only the generated square image and synthetic questions were sent, not a user screenshot.

## Acceptance boundary

Suitable for main as an opt-in experimental image API and off-by-default shadow, subject to exact-head CI. This is not production vision certification or threshold calibration: TypeSafe labels Images and Box as Dreamer-ready, and the 0.6 shadow threshold remains uncalibrated on a labelled image corpus. Header checks are not a full decoder; corrupt payloads may still be rejected by the API. No GUI code executes Box coordinates. No cloud screenshot forwarding, public release or merge was performed by this review.
