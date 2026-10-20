import 'package:flutter_test/flutter_test.dart';
import 'package:smart_meeting_minutes/validators.dart';

void main() {
  test('email', () {
    expect(Validators.email(''), isNotNull);
    expect(Validators.email('nope'), isNotNull);
    expect(Validators.email(' a@b.co '), isNull);
  });

  test('password rules differ for sign in and sign up', () {
    expect(Validators.password(''), isNotNull);
    expect(Validators.password('short'), isNull);
    expect(Validators.password('short', strict: true), isNotNull);
    expect(Validators.password('long-enough', strict: true), isNull);
    expect(Validators.confirm('a', 'b'), isNotNull);
    expect(Validators.confirm('a', 'a'), isNull);
  });

  test('server url', () {
    expect(Validators.serverUrl(''), isNotNull);
    expect(Validators.serverUrl('ftp://x.com'), isNotNull);
    expect(Validators.serverUrl('https://user:pw@x.com'), isNotNull);
    expect(Validators.serverUrl('http://10.0.2.2:5000'), isNull);
    expect(Validators.serverUrl('https://api.example.com'), isNull);
  });

  group('jitsi target', () {
    String? v(String link, String room, {bool req = true}) => Validators.jitsiTarget(link: link, room: room, requireOne: req);
    test('valid links and rooms', () {
      expect(v('https://meet.jit.si/Team-Standup', ''), isNull);
      expect(v('meet.jit.si/Team', ''), isNull);
      expect(v('https://jitsi.example.org:8443/tenant/room-1', ''), isNull);
      expect(v('', 'my-room'), isNull);
    });
    test('invalid', () {
      expect(v('', ''), isNotNull);
      expect(v('', '', req: false), isNull); // Auto mode
      expect(v('http://meet.jit.si/x', ''), isNotNull);
      expect(v('https://meet.jit.si/', ''), isNotNull);
      expect(v('https://meet.jit.si/..', ''), isNotNull); // matches the backend fix
      expect(v('https://meet.jit.si/a/b/c', ''), isNotNull);
      expect(v('', 'bad room!'), isNotNull);
    });
  });
}
