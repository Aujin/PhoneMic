# PhoneMic

Experimental LAN audio transport for using a phone microphone as a Windows audio input. The repository currently contains a Windows receiver and a Python test-tone sender. A phone app and microphone capture are **not implemented yet**.

## Current prototype

- `ios_sender_mock/sender_mock.py` generates a 1 kHz tone, Opus-encodes it and sends UDP packets.
- `windows_receiver/receiver.py` receives and decodes the packets and plays PCM to an output device. When VB-Cable is installed, it looks for `CABLE Input`; applications can then record from `CABLE Output`.
- The local `flutter/` directory is a Flutter SDK checkout, not a PhoneMic app, and is excluded from Git.

## Run the prototype

Install Python 3.11 or newer and the dependencies on each machine running a Python component:

```powershell
python -m pip install -r windows_receiver/requirements.txt
```

On Windows:

```powershell
python windows_receiver/receiver.py --list-devices
python windows_receiver/receiver.py --port 50000
```

On another machine on the same LAN, replace the destination with the Windows PC's LAN IP address:

```powershell
python ios_sender_mock/sender_mock.py --dest 192.168.1.20 --port 50000 --seconds 10
```

Allow inbound UDP port 50000 in Windows Firewall if needed. Without VB-Cable the receiver plays to the default output device, which does not make it a microphone input.

The wire format is a 10-byte header (`!BBHIH`: type, 8-bit sequence, 16-bit sequence, 48 kHz sample timestamp, payload length) followed by an Opus packet. Defaults are mono, 48 kHz, 20 ms frames and 64 kb/s.

## Tests and limitations

```powershell
python -m unittest discover -s windows_receiver -p 'test_*.py' -v
python ios_sender_mock/pyogg_smoke.py
```

The unit tests cover PCM buffering across output callbacks. The Opus smoke test requires the dependencies above. The full LAN and VB-Cable path still needs a real end-to-end test. Device discovery, pairing, authentication, packet reordering and a phone app are not implemented.
