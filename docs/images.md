# Images and Box questions

TypeSafe's System One accepts PNG and JPEG images in the state, instructions and criteria,
and a `box` question that says where something is in an image (docs.typesafe.ai/images,
docs.typesafe.ai/primitives/box). Both are **Dreamer-ready**: not production-ready, and the
request format, answers and limits may change. This repo uses them in shadow only.

## In code

```python
from jevkit import client, images

ref, picture = images.asset("/tmp/render.png")          # checked against the API's limits
out = client.ask({"render": ref},
                 {"clipped": client.noul("Is any text in `render` cut off?"),
                  "where": client.box("the Submit button in `render`")},
                 assets={ref: picture}, model="jev-1.14.0")
```

- `images.asset` reads the header only. Nothing is resized, rotated or re-encoded (standard
  library only), so an image the API would refuse raises `images.ImageError` with a short code
  (`side_out_of_range`, `progressive_jpeg`, `animated_png`, `exif_rotated`, ...) and the caller fails open.
  A JPEG whose EXIF says it is stored rotated is refused, because the API ignores EXIF
  orientation and the model would see it on its side. Prepare pictures upright, at most 1568 px on the longest side, as PNG or baseline JPEG.
- Images and box questions go to TypeSafe (or an explicit `TYPESAFE_BASE_URL`) only. Through
  OpenRouter, Venice or Zen, `ask` raises `JevError("images_unsupported")` without sending.
- A box always comes back: there is no "not found". Ask a `noul` beside it and ignore the box
  when that is low. Box needs Jev 1.14; `jev-latest` may not be it yet.
- Images are billed as input tokens, like text.

## In Hermes: the cloud vision shadow

`/jev vision_cloud shadow` asks TypeSafe Jev the same decision-shaped `vision_analyze`
questions the local Jev-Omni shadow (`/jev vision shadow`) answers, and logs one row per call
to `logs/jev-vision.jsonl` with `model: typesafe-jev`. The two switches are independent, so
both models can be compared against the helper model's answer on the same calls.

**This sends the picture to TypeSafe.** The question goes through `privacy.redact`, and a
question that looks sensitive is skipped, but a screenshot cannot be screened. Turn it on only
on machines whose `vision_analyze` pictures may leave the machine. `touch
~/.hermes/jev/VISION_CLOUD_OFF` stops it at once.

It shares the local shadow's sampling (`vision_sample`) and daily ceiling (`vision_daily_max`),
skips calls with a `region` (no cropping without an image library), and never changes a tool
result. The row holds ids, hashes, classes, confidence, tokens and timings, never the picture,
path, question or answer text. The cloud switch is rechecked immediately before sending,
so disabling it during image preparation prevents that upload; an HTTP request already
in flight cannot be recalled. The A/B runner also skips sensitive questions before reading
or sending their pictures.

Reference validation considers actual string values (including Unicode), never object keys
or JSON escaping. Whitespace-only references are refused locally. Header inspection is
not a full image decoder: a corrupt payload can still be rejected by the API, and callers
must retain their fail-open path.

## Before switching it on

Run these on the machine that holds the key (the key is never printed):

```bash
python3 scripts/live_images_check.py              # one request: Noul, Choice and Box on a drawn square
python3 scripts/live_images_check.py --no-box     # if the account is not on Jev 1.14 yet
python3 scripts/vision_cloud_ab.py labelled.jsonl --out vision-cloud-ab.json
```

`live_images_check.py` exits 0 only when TypeSafe accepts the request and answers a picture
with a known answer correctly (box overlap of at least 0.5). `vision_cloud_ab.py` reads the
hand-labelled checks (`{"image", "question", "label"}` per line), asks each the way the shadow
does, and reports accuracy and, per confidence cut-off, coverage and accuracy of the kept
checks. `THRESHOLD` in `jevkit/cloud_vision.py` is Jev-Omni's 0.6 until that report says
otherwise. The report holds counts only.
