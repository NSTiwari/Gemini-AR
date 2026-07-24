# Gemini AR

A mobile web app that turns a single photo into a real, camera-placed 3D object. Gemini edits the photo to add an object, Hyper3D Rodin generates a textured 3D mesh from it, and the browser's own AR support (WebXR, backed by ARCore) lets you place and view it in your actual room. No native app required.

## Demo

![Gemini AR demo](Gemini%20AR.gif)

## How it works

1. Take a photo of a room directly from your phone's camera.
2. Describe an object to add, for example "Add a blue sofa." Gemini (`gemini-3-pro-image`, Nano Banana Pro) edits the photo to place it naturally in the scene.
3. The app asks Gemini again to isolate just the added object into a clean, standalone product shot.
4. That isolated image is sent to Hyper3D Rodin v2.5 (via fal.ai), which generates a textured 3D mesh in both GLB and USDZ formats.
5. The mesh is rendered with `model-viewer`. Tapping "View in your room" launches a real AR session, WebXR on Android/Chrome or AR Quick Look on iOS/Safari, so the object appears anchored in your real room as you move the phone around.

## Setup

```
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and add your own keys, then run:

```
python app.py
```

Open `http://localhost:5001`.

To actually test the AR feature, the app needs to be reachable by a phone over HTTPS, since both WebXR and AR Quick Look require a secure context. A plain `http://localhost` will not trigger AR. Locally, a tool like `cloudflared tunnel --url http://127.0.0.1:5001` works well for this during development.

## Environment variables

See `.env.example`. You will need:

- `GEMINI_API_KEY`: a Google Gemini API key with access to image generation models
- `FAL_KEY`: a fal.ai API key with access to `fal-ai/hyper3d/rodin/v2.5`

## Notes and limitations

- This is a demo/prototype, not a production service. There is no authentication, and uploaded images are stored unencrypted on local disk.
- iOS AR (Quick Look) depends on Rodin returning a valid USDZ file for that specific generation. If it does not, AR still works on Android/WebXR but not on iOS for that object.
- The in-memory job store for 3D generation is per-process. Restarting the server clears any in-progress generation.
