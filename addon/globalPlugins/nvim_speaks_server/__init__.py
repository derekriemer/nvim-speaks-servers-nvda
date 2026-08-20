import json
import socket
import threading
import wave
from pathlib import Path
from typing import Optional

import globalPluginHandler
import queueHandler
import speech
import synthDriverHandler
import tones
from logHandler import log
from speech.commands import (
	BeepCommand,
	BreakCommand,
	CharacterModeCommand,
	LangChangeCommand,
	PitchCommand,
	RateCommand,
	VolumeCommand,
	WaveFileCommand,
)
from scriptHandler import script
from speech.priorities import SpeechPriority


HOST = "127.0.0.1"
PORT = 7533
BACKEND = "nvda-addon"
RECV_BYTES = 16384
JOIN_TIMEOUT = 2.0
EARCON_DIR = Path(__file__).with_name("earcons")


def _wav_duration_ms(path: Path) -> int:
	with wave.open(str(path), "rb") as wav:
		return int(round(wav.getnframes() * 1000 / wav.getframerate()))


EARCONS = {path.stem: (str(path), _wav_duration_ms(path)) for path in sorted(EARCON_DIR.glob("*.wav"))}


def _number(value) -> Optional[float]:
	if isinstance(value, bool):
		return None
	if isinstance(value, (int, float)):
		return float(value)
	return None


def _positive_int(value) -> Optional[int]:
	number = _number(value)
	if number is None or number <= 0:
		return None
	return int(round(number))


def _volume_percent(value, default: int = 50) -> int:
	number = _number(value)
	if number is None:
		return default
	return max(0, min(100, int(round(number))))


def _supported_commands() -> list[str]:
	commands = ["text", "beep", "earcon"]
	synth = synthDriverHandler.getSynth()
	supported = getattr(synth, "supportedCommands", set()) if synth else set()
	if CharacterModeCommand in supported:
		commands.append("char_mode")
	if PitchCommand in supported:
		commands.append("pitch")
	if RateCommand in supported:
		commands.append("rate")
	if VolumeCommand in supported:
		commands.append("volume")
	if BreakCommand in supported:
		commands.append("break")
	if LangChangeCommand in supported:
		commands.append("lang")
	return commands


def _supported_earcons() -> list[str]:
	return sorted(EARCONS.keys())


def _speech_priority(priority_name: Optional[str], interrupt: bool) -> SpeechPriority:
	if interrupt:
		return SpeechPriority.NOW
	if priority_name == "polite":
		return SpeechPriority.NEXT
	return SpeechPriority.NORMAL


def _speak_on_event_queue(sequence: list, priority_name: Optional[str], interrupt: bool) -> None:
	if interrupt:
		speech.cancelSpeech()
	speech.speak(sequence, priority=_speech_priority(priority_name, interrupt))


def _sequence_from_commands(sequence, fallback_text: str) -> list:
	if not isinstance(sequence, list):
		return [fallback_text] if fallback_text else []

	out = []
	for item in sequence:
		if not isinstance(item, dict):
			continue
		cmd = item.get("cmd")
		if cmd == "text":
			text = item.get("s")
			if isinstance(text, str) and text:
				out.append(text)
		elif cmd == "beep":
			hz = _positive_int(item.get("hz"))
			ms = _positive_int(item.get("ms"))
			if hz is not None and ms is not None:
				out.append(
					BeepCommand(
						hz,
						ms,
						left=_volume_percent(item.get("left")),
						right=_volume_percent(item.get("right")),
					),
				)
		elif cmd == "earcon":
			earcon_id = item.get("id")
			if isinstance(earcon_id, str) and (earcon := EARCONS.get(earcon_id)) is not None:
				earcon_path, duration_ms = earcon
				out.append(WaveFileCommand(earcon_path))
				if item.get("blocking") is not False:
					out.append(BreakCommand(time=duration_ms))
		elif cmd == "char_mode":
			if isinstance(item.get("on"), bool):
				out.append(CharacterModeCommand(item["on"]))
		elif cmd == "pitch":
			multiplier = _number(item.get("multiplier"))
			if multiplier is not None:
				out.append(PitchCommand(multiplier=multiplier))
		elif cmd == "rate":
			multiplier = _number(item.get("multiplier"))
			if multiplier is not None:
				out.append(RateCommand(multiplier=multiplier))
		elif cmd == "volume":
			multiplier = _number(item.get("multiplier"))
			if multiplier is not None:
				out.append(VolumeCommand(multiplier=multiplier))
		elif cmd == "break":
			ms = _positive_int(item.get("ms"))
			if ms is not None:
				out.append(BreakCommand(time=ms))
		elif cmd == "lang":
			lang = item.get("id")
			if isinstance(lang, str):
				out.append(LangChangeCommand(lang))

	if out:
		return out
	return [fallback_text] if fallback_text else []


class ClientConnection:
	def __init__(self, server: "NvimSpeaksServer", client_socket: socket.socket, address) -> None:
		self.server = server
		self.socket = client_socket
		self.address = address
		self._buffer = b""
		self._closed = threading.Event()
		self.thread = threading.Thread(
			target=self._run,
			name="nvim-speaks NVDA client",
			daemon=True,
		)

	def start(self) -> None:
		self._send_json({"type": "ready", "backend": BACKEND})
		self._send_capabilities()
		self.thread.start()

	def close(self) -> None:
		if self._closed.is_set():
			return
		self._closed.set()
		try:
			self.socket.shutdown(socket.SHUT_RDWR)
		except OSError:
			pass
		try:
			self.socket.close()
		except OSError:
			pass

	def join(self) -> None:
		if self.thread.is_alive():
			self.thread.join(JOIN_TIMEOUT)

	def _run(self) -> None:
		try:
			while not self._closed.is_set() and not self.server.stopping:
				try:
					data = self.socket.recv(RECV_BYTES)
				except OSError:
					break
				if not data:
					break
				self._buffer += data
				while b"\n" in self._buffer:
					line, self._buffer = self._buffer.split(b"\n", 1)
					self._handle_line(line.rstrip(b"\r"))
		finally:
			self.close()
			self.server.remove_client(self)

	def _handle_line(self, line: bytes) -> None:
		if not line:
			return
		try:
			message = json.loads(line.decode("utf-8"))
		except (UnicodeDecodeError, json.JSONDecodeError) as exc:
			self._send_error(None, "invalid JSON: %s" % exc)
			return
		if not isinstance(message, dict):
			self._send_error(None, "message must be a JSON object")
			return
		self._handle_message(message)

	def _handle_message(self, message: dict) -> None:
		if message.get("type") == "query_capabilities":
			self._send_capabilities()
			return

		speech_message = message.get("speech")
		if speech_message is None:
			return
		if not isinstance(speech_message, dict):
			self._send_error(message.get("seq"), "speech must be a JSON object")
			return

		text = speech_message.get("text", "")
		if text is None:
			text = ""
		if not isinstance(text, str):
			self._send_error(message.get("seq"), "speech.text must be a string")
			return

		sequence = _sequence_from_commands(speech_message.get("sequence"), text)
		if not sequence:
			return
		priority_name = speech_message.get("priority")
		if priority_name is not None and not isinstance(priority_name, str):
			priority_name = None
		interrupt = speech_message.get("interrupt") is True
		queueHandler.queueFunction(
			queueHandler.eventQueue,
			_speak_on_event_queue,
			sequence,
			priority_name,
			interrupt,
		)

	def _send_error(self, seq, message: str) -> None:
		payload = {"type": "error", "message": message}
		if seq is not None:
			payload["seq"] = seq
		self._send_json(payload)

	def _send_json(self, payload: dict) -> None:
		try:
			data = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
			self.socket.sendall(data)
		except OSError:
			self.close()

	def _send_capabilities(self) -> None:
		self._send_json(
			{
				"type": "capabilities",
				"backend": BACKEND,
				"commands": _supported_commands(),
				"earcons": _supported_earcons(),
			},
		)


class NvimSpeaksServer:
	def __init__(self, host: str = HOST, port: int = PORT) -> None:
		self.host = host
		self.port = port
		self._stop = threading.Event()
		self._clients_lock = threading.Lock()
		self._clients: list[ClientConnection] = []
		self._server_socket: Optional[socket.socket] = None
		self._thread: Optional[threading.Thread] = None

	@property
	def stopping(self) -> bool:
		return self._stop.is_set()

	def start(self) -> None:
		if self._thread is not None and self._thread.is_alive():
			return
		self._stop.clear()
		self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		self._server_socket.bind((self.host, self.port))
		self._server_socket.listen(5)
		self._server_socket.settimeout(0.2)
		self._thread = threading.Thread(
			target=self._run,
			name="nvim-speaks NVDA server",
			daemon=True,
		)
		self._thread.start()
		log.info("nvim-speaks NVDA server listening on %s:%s", self.host, self.port)

	def stop(self) -> None:
		self._stop.set()
		if self._server_socket is not None:
			try:
				self._server_socket.close()
			except OSError:
				pass
			self._server_socket = None
		with self._clients_lock:
			clients = list(self._clients)
		for client in clients:
			client.close()
		if self._thread is not None and self._thread.is_alive():
			self._thread.join(JOIN_TIMEOUT)
		for client in clients:
			client.join()
		log.info("nvim-speaks NVDA server stopped")

	def remove_client(self, client: ClientConnection) -> None:
		with self._clients_lock:
			try:
				self._clients.remove(client)
			except ValueError:
				pass

	def _run(self) -> None:
		server_socket = self._server_socket
		if server_socket is None:
			return
		try:
			while not self._stop.is_set():
				try:
					client_socket, address = server_socket.accept()
				except socket.timeout:
					continue
				except OSError:
					break
				try:
					client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
				except OSError:
					pass
				client = ClientConnection(self, client_socket, address)
				with self._clients_lock:
					self._clients.append(client)
				client.start()
		except Exception:
			log.exception("nvim-speaks NVDA server failed")
		finally:
			self._stop.set()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self) -> None:
		super().__init__()
		self.server = NvimSpeaksServer()
		try:
			self.server.start()
		except OSError:
			log.exception("Could not start nvim-speaks NVDA server")
			self.server = None

	def terminate(self) -> None:
		if self.server is not None:
			self.server.stop()
		super().terminate()

	@script(gesture="kb:nvda+shift+s")
	def script_toggleServer(self, gesture) -> None:
		"""Stops or restarts the nvim-speaks speech server."""
		if self.server is not None and not self.server.stopping:
			self.server.stop()
			self.server = None
			tones.beep(500, 100)
			return
		self.server = NvimSpeaksServer()
		try:
			self.server.start()
		except OSError:
			log.exception("Could not restart nvim-speaks NVDA server")
			self.server = None
			tones.beep(250, 200)
			return
		tones.beep(1000, 100)
