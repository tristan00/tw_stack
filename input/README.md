# input — capture stream: system-wide keyboard / mouse / focus

Polls Win32 for the whole desktop and emits `focus / move / key_down / key_up / mouse_down /
mouse_up` rows. Sets `ctx.shot_req` on every click, which is what couples input to shots. Never
touches WH3.

This is a SYSTEM-WIDE recorder — every key and click in every window. It is not narrowed at write
time; that is the user's call, not the recorder's.

## API

```python
input_stream.run(ctx, probes=None)
```

`probes` are the three injectable OS reads (cursor, foreground, keystate); the default is real
Win32. Started by the manager only under `--input`.
