import importlib.util
import sys
import types
import time


def _load_spotify_channel():
    # Load channel module dynamically similar to runtime
    from pathlib import Path
    channel_dir = Path(__file__).parent.parent / "channels" / "spotify_status"
    channel_path = channel_dir / "channel.py"
    spec = importlib.util.spec_from_file_location("test_spotify_channel", channel_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore
    sys.modules[spec.name] = mod  # type: ignore
    spec.loader.exec_module(mod)  # type: ignore
    return channel_dir, mod.ChannelClass  # type: ignore


def test_spotify_manifest_basic():
    channel_dir, ChannelClass = _load_spotify_channel()
    ch = ChannelClass(str(channel_dir))
    manifest = ch.get_manifest()
    assert manifest["id"].startswith("com.spotify") or manifest["id"].endswith("spotify_status")
    assert "version" in manifest


def test_spotify_push_change_detection():
    # Load push module directly and simulate metadata changes
    from pathlib import Path
    channel_dir, ChannelClass = _load_spotify_channel()
    push_path = channel_dir / "push.py"
    spec = importlib.util.spec_from_file_location("test_spotify_push", push_path)
    push_mod = importlib.util.module_from_spec(spec)  # type: ignore
    sys.modules[spec.name] = push_mod  # type: ignore
    spec.loader.exec_module(push_mod)  # type: ignore

    PushManager = getattr(push_mod, "PushManager")

    events = []
    def emitter(evt):
        events.append(evt)

    pm = PushManager(emitter=emitter, poll_interval=0.01)

    # Inject a fake service with get_current_track
    class FakeService:
        def __init__(self):
            self.calls = 0
        def get_current_track(self):
            self.calls += 1
            if self.calls == 1:
                return {"artist_name": "A", "album_name": "B", "track_name": "C"}
            elif self.calls == 2:
                return {"artist_name": "A", "album_name": "B", "track_name": "C"}  # no change
            else:
                return {"artist_name": "X", "album_name": "Y", "track_name": "Z"}  # change triggers

    pm.spotify_service = FakeService()  # type: ignore
    pm.start()
    # Let it poll a few times
    time.sleep(0.05)
    pm.stop()

    # Expect only one change event (initial does not emit, change on third call emits)
    change_events = [e for e in events if e.get("type") == "track_change"]
    assert len(change_events) == 1, events
