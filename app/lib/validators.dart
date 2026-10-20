/// Pure input validation, shared by the forms and unit-tested.
class Validators {
  static final _email = RegExp(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$');

  static String? email(String? v) {
    final s = (v ?? '').trim();
    if (s.isEmpty) return 'Enter your email address.';
    if (!_email.hasMatch(s)) return 'Enter a valid email address.';
    return null;
  }

  static String? password(String? v, {bool strict = false}) {
    final s = v ?? '';
    if (s.isEmpty) return 'Enter your password.';
    if (strict && s.length < 8) return 'Use at least 8 characters.';
    return null;
  }

  static String? confirm(String? v, String original) =>
      v == original ? null : 'The passwords do not match.';

  static String? userName(String? v) {
    final s = (v ?? '').trim();
    if (s.isEmpty) return 'Enter the name to show in the meeting.';
    if (s.length > 60) return 'Use 60 characters or fewer.';
    return null;
  }

  /// Backend / bot server URL: http(s) with a host. (http is allowed so a LAN or emulator backend works in development.)
  static String? serverUrl(String? v) {
    final s = (v ?? '').trim();
    if (s.isEmpty) return 'Enter the backend server URL.';
    final u = Uri.tryParse(s);
    if (u == null || !(u.scheme == 'http' || u.scheme == 'https') || u.host.isEmpty) {
      return 'Enter a URL such as https://api.example.com';
    }
    if (u.userInfo.isNotEmpty) return 'The URL must not contain a username or password.';
    return null;
  }

  static final _roomPart = RegExp(r'^[\w.\-~]{1,200}$', unicode: true);

  static bool _validRoomPart(String p) => _roomPart.hasMatch(p) && p.replaceAll('.', '').isNotEmpty;

  /// A Jitsi link (https) or a bare room name. Returns null when valid. Both fields empty is valid only in Auto mode.
  static String? jitsiTarget({required String link, required String room, required bool requireOne}) {
    final l = link.trim(), r = room.trim();
    if (l.isEmpty && r.isEmpty) return requireOne ? 'Enter a Jitsi meeting link or a room name.' : null;
    if (l.isNotEmpty) {
      final u = Uri.tryParse(l.contains('//') ? l : 'https://$l');
      if (u == null || u.scheme != 'https' || u.host.isEmpty || u.userInfo.isNotEmpty) {
        return 'Enter a valid https Jitsi meeting link.';
      }
      final parts = u.pathSegments.where((p) => p.isNotEmpty).toList();
      if (parts.isEmpty || parts.length > 2 || !parts.every(_validRoomPart)) {
        return 'The link does not contain a valid room name.';
      }
      return null;
    }
    final parts = r.split('/').where((p) => p.isNotEmpty).toList();
    if (parts.isEmpty || parts.length > 2 || !parts.every(_validRoomPart)) return 'Enter a valid room name.';
    return null;
  }
}
