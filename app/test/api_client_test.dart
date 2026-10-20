import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:smart_meeting_minutes/api_client.dart';
import 'package:smart_meeting_minutes/models.dart';

ApiClient client(MockClient mock, {String? token = 'tok'}) =>
    ApiClient(baseUrl: 'http://host:5000/', tokenProvider: () async => token, client: mock);

http.Response jsonResp(int status, Object body) => http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json'});

void main() {
  test('sends the bearer token and builds the URL without double slashes', () async {
    late http.Request seen;
    final api = client(MockClient((r) async {
      seen = r;
      return jsonResp(200, {'meetings': []});
    }));
    await api.listMeetings(limit: 10);
    expect(seen.url.toString(), 'http://host:5000/api/meetings?limit=10');
    expect(seen.headers['Authorization'], 'Bearer tok');
  });

  test('health is public (no Authorization header)', () async {
    late http.Request seen;
    final api = client(MockClient((r) async {
      seen = r;
      return jsonResp(200, {'ok': true, 'asr': {'available': true}, 'llm': {'configured': false}, 'store': 'memory'});
    }), token: null);
    final h = await api.health();
    expect(seen.headers.containsKey('Authorization'), isFalse);
    expect(h.ok && h.asrAvailable && !h.llmConfigured, isTrue);
  });

  test('missing token fails before any request', () async {
    var called = false;
    final api = client(MockClient((r) async {
      called = true;
      return jsonResp(200, {});
    }), token: null);
    await expectLater(api.listMeetings(), throwsA(isA<ApiException>().having((e) => e.status, 'status', 401)));
    expect(called, isFalse);
  });

  test('createMeeting sends consent and omits empty link/room (Auto)', () async {
    late Map body;
    final api = client(MockClient((r) async {
      body = jsonDecode(r.body) as Map;
      return jsonResp(201, {'meeting': {'id': 'm1', 'status': 'created'}, 'reused': false});
    }));
    final r = await api.createMeeting(displayName: ' Alice ', consent: true);
    expect(body['consent'], true);
    expect(body['display_name'], 'Alice');
    expect(body.containsKey('meeting_url'), isFalse);
    expect(body.containsKey('room_name'), isFalse);
    expect(r.meeting.id, 'm1');
    expect(r.reused, isFalse);
  });

  test('backend error codes become friendly messages', () async {
    final api = client(MockClient((r) async => jsonResp(400, {'error': {'code': 'INVALID_MEETING_LINK', 'message': 'raw'}})));
    await expectLater(
      api.createMeeting(displayName: 'A', consent: true, meetingUrl: 'x'),
      throwsA(isA<ApiException>().having((e) => e.code, 'code', 'INVALID_MEETING_LINK').having((e) => e.message, 'message', contains('valid Jitsi'))),
    );
  });

  test('non-JSON error page and non-JSON success are handled', () async {
    final bad = client(MockClient((r) async => http.Response('<html>502</html>', 502)));
    await expectLater(bad.status('m'), throwsA(isA<ApiException>().having((e) => e.status, 'status', 502)));
    final odd = client(MockClient((r) async => http.Response('<html>hello</html>', 200)));
    await expectLater(odd.health(), throwsA(isA<ApiException>().having((e) => e.code, 'code', 'BAD_RESPONSE')));
  });

  test('network failure is reported as a network error', () async {
    final api = client(MockClient((r) async => throw http.ClientException('boom')));
    await expectLater(api.status('m'), throwsA(isA<ApiException>().having((e) => e.isNetwork, 'isNetwork', true)));
  });

  test('minutes not ready is a clear 409', () async {
    final api = client(MockClient((r) async => jsonResp(409, {'error': {'code': 'MINUTES_NOT_READY', 'message': 'x'}})));
    await expectLater(api.minutes('m'), throwsA(isA<ApiException>().having((e) => e.status, 'status', 409)));
  });

  test('export returns the file bytes and passes the format', () async {
    late Uri seen;
    final api = client(MockClient((r) async {
      seen = r.url;
      return http.Response('hello', 200);
    }));
    final bytes = await api.export('m1', 'txt');
    expect(utf8.decode(bytes), 'hello');
    expect(seen.queryParameters['format'], 'txt');
  });

  test('a failed TLS handshake is reported as a network error with advice', () async {
    final api = client(MockClient((r) async => throw const HandshakeException('bad certificate')));
    await expectLater(
      api.status('m'),
      throwsA(isA<ApiException>().having((e) => e.isNetwork, 'isNetwork', true).having((e) => e.message, 'message', contains('secure connection'))),
    );
  });

  test('error codes the backend can send have friendly messages', () {
    for (final code in ['BOT_SERVER_NOT_CONFIGURED', 'MEETING_NOT_JOINABLE', 'NOT_RETRYABLE', 'INVALID_TOKEN', 'PASSWORD_REQUIRED', 'LOBBY_TIMEOUT']) {
      expect(friendlyError(code, 'raw server text'), isNot('raw server text'), reason: code);
    }
    expect(friendlyError('SOMETHING_NEW', 'raw server text'), 'raw server text'); // unknown codes keep the server's text
  });

  test('status parsing drives the UI state', () async {
    final api = client(MockClient((r) async => jsonResp(200, {'id': 'm1', 'status': 'joined', 'audio_confirmed': false, 'participants': []})));
    final m = await api.status('m1');
    expect(m.status, MeetingStatus.joined);
    expect(m.audioConfirmed, isFalse); // "joined" must never imply audio is being captured
  });
}
