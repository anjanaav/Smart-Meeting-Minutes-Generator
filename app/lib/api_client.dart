import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'models.dart';

class ApiException implements Exception {
  final int status;
  final String code, message;
  const ApiException(this.status, this.code, this.message);
  bool get isNetwork => status == 0;
  bool get isUnauthorized => status == 401;
  @override
  String toString() => message;
}

typedef TokenProvider = Future<String?> Function();

/// Friendly text for backend error codes (docs/API.md). Falls back to the server's own message.
String friendlyError(String code, String fallback) => switch (code) {
      'INVALID_MEETING_LINK' => 'That is not a valid Jitsi meeting link.',
      'HOST_UNRESOLVABLE' => 'The Jitsi server in this link could not be found.',
      'HOST_NOT_ALLOWED' => 'This Jitsi server is not allowed by the backend.',
      'CONSENT_REQUIRED' => 'Confirm that all participants agree to the meeting being recorded and transcribed.',
      'BOT_UNREACHABLE' => 'The bot server could not be reached. Check that it is running.',
      'BOT_REJECTED' => 'The bot server refused the request. Check the shared secret configuration.',
      'BOT_SERVER_NOT_CONFIGURED' => 'The backend has no bot server configured.',
      'MEETING_NOT_JOINABLE' => 'This meeting has already ended. Start a new one.',
      'NOT_RETRYABLE' => 'This meeting cannot be retried.',
      'INVALID_TOKEN' || 'AUTH_REQUIRED' => 'Your session has expired. Please sign in again.',
      'PROCESSING_ERROR' => 'Something went wrong while generating the minutes.',
      'BOT_SERVER_REQUIRED' => 'The backend has no bot server configured.',
      'BOT_SERVER_NOT_ALLOWED' => 'This bot server is not allowed by the backend.',
      'PASSWORD_REQUIRED' => 'This meeting needs a password, so the bot could not join.',
      'LOBBY_REJECTED' => 'The host did not admit the bot to the meeting.',
      'LOBBY_TIMEOUT' => 'No one admitted the bot in time. Ask the host to admit it and try again.',
      'MEETING_NOT_FOUND' => 'The meeting could not be found on the Jitsi server.',
      'AUDIO_CAPTURE_UNAVAILABLE' => 'The bot joined but could not capture meeting audio.',
      'JOIN_FAILED' => 'The bot could not join the meeting.',
      'BOT_CRASHED' => 'The bot stopped unexpectedly.',
      'BOT_TIMEOUT' => 'The bot stopped responding.',
      'NO_AUDIO' => 'No audio was captured during the meeting.',
      'EMPTY_TRANSCRIPT' => 'No intelligible speech was recognised in the meeting.',
      'ASR_FAILED' => 'Speech recognition failed.',
      'ASR_UNAVAILABLE' => 'The speech recognition service is not available.',
      'AI_OUTPUT_INVALID' => 'The minutes could not be generated (invalid AI output).',
      'DATABASE_ERROR' || 'DATABASE_UNAVAILABLE' => 'The database is temporarily unavailable. Try again shortly.',
      'MINUTES_NOT_READY' => 'The minutes are not ready yet.',
      _ => fallback,
    };

class ApiClient {
  ApiClient({required String baseUrl, required this.tokenProvider, http.Client? client, this.timeout = const Duration(seconds: 25)})
      : baseUrl = baseUrl.trim().replaceAll(RegExp(r'/+$'), ''),
        _client = client ?? http.Client();

  final String baseUrl;
  final TokenProvider tokenProvider;
  final Duration timeout;
  final http.Client _client;

  Uri _uri(String path, [Map<String, String>? query]) {
    final u = Uri.parse('$baseUrl/api$path');
    return query == null ? u : u.replace(queryParameters: query);
  }

  Future<http.Response> _send(String method, String path, {Map<String, String>? query, Object? body, bool auth = true}) async {
    final headers = <String, String>{'Accept': 'application/json'};
    if (auth) {
      final token = await tokenProvider();
      if (token == null) throw const ApiException(401, 'AUTH_REQUIRED', 'Please sign in again.');
      headers['Authorization'] = 'Bearer $token';
    }
    if (body != null) headers['Content-Type'] = 'application/json';
    try {
      final req = http.Request(method, _uri(path, query))..headers.addAll(headers);
      if (body != null) req.body = jsonEncode(body);
      final streamed = await _client.send(req).timeout(timeout);
      return await http.Response.fromStream(streamed).timeout(timeout);
    } on TimeoutException {
      throw const ApiException(0, 'NETWORK', 'The server took too long to answer. Check the server URL and your connection.');
    } on HandshakeException {
      throw const ApiException(0, 'NETWORK', 'A secure connection could not be made. Check that the URL starts with the right http:// or https:// and that the server certificate is valid.');
    } on SocketException {
      throw const ApiException(0, 'NETWORK', 'Cannot reach the server. Check the server URL and your connection.');
    } on http.ClientException {
      throw const ApiException(0, 'NETWORK', 'Cannot reach the server. Check the server URL and your connection.');
    } on FormatException {
      throw const ApiException(0, 'NETWORK', 'The server URL is not valid.');
    }
  }

  ApiException _error(http.Response r) {
    String code = 'HTTP_${r.statusCode}', message = 'The server answered HTTP ${r.statusCode}.';
    try {
      final j = jsonDecode(utf8.decode(r.bodyBytes));
      final e = j is Map ? j['error'] : null;
      if (e is Map) {
        code = (e['code'] as String?) ?? code;
        message = (e['message'] as String?) ?? message;
      }
    } catch (_) {
      // Not JSON (for example a proxy error page): keep the generic message.
    }
    return ApiException(r.statusCode, code, friendlyError(code, message));
  }

  Future<dynamic> _json(String method, String path, {Map<String, String>? query, Object? body, bool auth = true}) async {
    final r = await _send(method, path, query: query, body: body, auth: auth);
    if (r.statusCode < 200 || r.statusCode >= 300) throw _error(r);
    try {
      return r.bodyBytes.isEmpty ? <String, dynamic>{} : jsonDecode(utf8.decode(r.bodyBytes));
    } on FormatException {
      throw ApiException(r.statusCode, 'BAD_RESPONSE', 'The server sent an unexpected response. Is this the right server URL?');
    }
  }

  // ---- public
  Future<Health> health() async => Health.fromJson(await _json('GET', '/health', auth: false));

  // ---- profile
  Future<void> saveProfile(String displayName) => _json('POST', '/users/profile', body: {'display_name': displayName});

  // ---- meetings
  /// Creates the meeting record. With neither [meetingUrl] nor [roomName] the backend generates a room (Auto).
  Future<({Meeting meeting, bool reused})> createMeeting({
    String? meetingUrl,
    String? roomName,
    required String displayName,
    String? title,
    required bool consent,
  }) async {
    final j = await _json('POST', '/meetings', body: {
      if (meetingUrl != null && meetingUrl.trim().isNotEmpty) 'meeting_url': meetingUrl.trim(),
      if (roomName != null && roomName.trim().isNotEmpty) 'room_name': roomName.trim(),
      'display_name': displayName.trim(),
      if (title != null && title.trim().isNotEmpty) 'title': title.trim(),
      'consent': consent,
    });
    return (meeting: Meeting.fromJson(j['meeting']), reused: j['reused'] == true);
  }

  Future<Meeting> joinBot(String meetingId, {String? botServerUrl}) async {
    final j = await _json('POST', '/meetings/$meetingId/bot/join', body: {
      if (botServerUrl != null && botServerUrl.trim().isNotEmpty) 'bot_server_url': botServerUrl.trim(),
    });
    return Meeting.fromJson(j['meeting']);
  }

  Future<List<Meeting>> listMeetings({int limit = 50}) async {
    final j = await _json('GET', '/meetings', query: {'limit': '$limit'});
    return (j['meetings'] as List? ?? const []).map(Meeting.fromJson).toList();
  }

  Future<Meeting> getMeeting(String id) async => Meeting.fromJson((await _json('GET', '/meetings/$id'))['meeting']);

  /// Live state straight from the backend. The UI must only show what this reports.
  Future<Meeting> status(String id) async => Meeting.fromJson(await _json('GET', '/meetings/$id/status'));

  Future<Minutes> minutes(String id) async => Minutes.fromJson(await _json('GET', '/meetings/$id/minutes'));

  Future<void> stop(String id) => _json('POST', '/meetings/$id/stop', body: <String, dynamic>{});

  Future<void> retry(String id) => _json('POST', '/meetings/$id/retry', body: <String, dynamic>{});

  Future<Uint8List> export(String id, String format) async {
    final r = await _send('GET', '/meetings/$id/export', query: {'format': format});
    if (r.statusCode != 200) throw _error(r);
    return r.bodyBytes;
  }

  void close() => _client.close();
}
